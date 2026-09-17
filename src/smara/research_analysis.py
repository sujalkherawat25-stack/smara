"""Deterministic, provenance-bound analysis for research datasets."""
from __future__ import annotations
import hashlib,json,math,statistics
from collections import defaultdict
from datetime import datetime,timedelta
from typing import Any,Iterable,Mapping

class ResearchAnalysisError(ValueError):pass

def _number(value:Any)->float|None:
    if value is None or isinstance(value,bool):return None
    try: result=float(str(value).replace(",","").strip())
    except (TypeError,ValueError):return None
    return result if math.isfinite(result) else None

def _quantile(values:list[float],fraction:float)->float:
    if len(values)==1:return values[0]
    position=(len(values)-1)*fraction;lower=math.floor(position);upper=math.ceil(position)
    return values[lower] if lower==upper else values[lower]*(upper-position)+values[upper]*(position-lower)

def _describe(rows:list[dict[str,Any]],column:str)->dict[str,Any]:
    values=sorted(value for row in rows if (value:=_number(row.get(column))) is not None)
    if not values:raise ResearchAnalysisError(f"numeric column has no finite values: {column}")
    return {"count":len(values),"missing":len(rows)-len(values),"sum":sum(values),"mean":statistics.fmean(values),"median":statistics.median(values),"stddev_sample":statistics.stdev(values) if len(values)>1 else None,"min":values[0],"q1":_quantile(values,.25),"q3":_quantile(values,.75),"max":values[-1]}

def _correlation(rows:list[dict[str,Any]],left:str,right:str)->dict[str,Any]:
    pairs=[(a,b) for row in rows if (a:=_number(row.get(left))) is not None and (b:=_number(row.get(right))) is not None]
    if len(pairs)<2:return {"left":left,"right":right,"count":len(pairs),"pearson":None,"reason":"fewer_than_two_pairs"}
    xs,ys=zip(*pairs);mx=statistics.fmean(xs);my=statistics.fmean(ys);numerator=sum((x-mx)*(y-my) for x,y in pairs);denominator=math.sqrt(sum((x-mx)**2 for x in xs)*sum((y-my)**2 for y in ys))
    return {"left":left,"right":right,"count":len(pairs),"pearson":numerator/denominator if denominator else None,"reason":None if denominator else "constant_series"}

def _time(value:Any)->datetime|None:
    try:return datetime.fromisoformat(str(value or "").strip().replace("Z","+00:00"))
    except ValueError:return None

def _linear_forecast(rows:list[dict[str,Any]],time_column:str,column:str,horizon:int)->dict[str,Any]:
    """Bounded ordinary-least-squares forecast with explicit extrapolation limits.

    This is intentionally labelled a forecast, not a causal or probabilistic
    guarantee.  The time axis is normalized to observation order so irregular
    dates cannot create an accidental unit mismatch; dates are only used for
    display of the projected timestamps.
    """
    if horizon<1 or horizon>30: raise ResearchAnalysisError("forecast horizon must be between 1 and 30")
    dated=sorted([(stamp,row) for row in rows if (stamp:=_time(row.get(time_column))) is not None and _number(row.get(column)) is not None],key=lambda item:item[0])
    if len(dated)<3: return {"column":column,"count":len(dated),"horizon":horizon,"status":"insufficient_history","reason":"at_least_three_dated_values_required","points":[]}
    ys=[float(_number(row.get(column))) for _,row in dated]; xs=list(range(len(ys))); mx=statistics.fmean(xs); my=statistics.fmean(ys)
    den=sum((x-mx)**2 for x in xs)
    slope=sum((x-mx)*(y-my) for x,y in zip(xs,ys))/den if den else 0.0; intercept=my-slope*mx
    residuals=[y-(intercept+slope*x) for x,y in zip(xs,ys)]
    rmse=math.sqrt(statistics.fmean([item*item for item in residuals])) if residuals else 0.0
    last_time=dated[-1][0]; step=(dated[-1][0]-dated[-2][0]) if len(dated)>1 else timedelta(days=1)
    if step.total_seconds()<=0: step=timedelta(days=1)
    points=[]
    for offset in range(1,horizon+1):
        x=len(ys)-1+offset; estimate=intercept+slope*x
        points.append({"step":offset,"timestamp":(last_time+step*offset).isoformat(),"value":estimate,"lower":estimate-1.96*rmse,"upper":estimate+1.96*rmse})
    return {"column":column,"count":len(ys),"horizon":horizon,"status":"ok","method":"ordinary_least_squares_time_index","slope":slope,"intercept":intercept,"rmse":rmse,"points":points,"warning":"extrapolation is observational and not causal"}

def _observational_effect(rows:list[dict[str,Any]],treatment_column:str,outcome_column:str,treatment_value:Any=None)->dict[str,Any]:
    if treatment_column not in {str(key) for row in rows for key in row} or outcome_column not in {str(key) for row in rows for key in row}:
        raise ResearchAnalysisError("causal analysis columns are missing")
    values=[(_number(row.get(outcome_column)),row.get(treatment_column)) for row in rows]
    usable=[item for item in values if item[0] is not None]
    if treatment_value is None:
        labels=[]
        for _,label in usable:
            if label not in labels: labels.append(label)
        if len(labels)!=2: return {"status":"insufficient_groups","groups":len(labels),"reason":"exactly_two_treatment_groups_required"}
        treatment_value=labels[1]
    treated=[value for value,label in usable if label==treatment_value]; control=[value for value,label in usable if label!=treatment_value]
    if not treated or not control: return {"status":"insufficient_groups","treated_count":len(treated),"control_count":len(control),"reason":"both_treatment_and_control_groups_required"}
    tm=statistics.fmean(treated); cm=statistics.fmean(control); pooled=math.sqrt(((len(treated)-1)*(statistics.variance(treated) if len(treated)>1 else 0)+ (len(control)-1)*(statistics.variance(control) if len(control)>1 else 0))/max(1,len(treated)+len(control)-2))
    return {"status":"ok","method":"observational_difference_in_means","treatment_column":treatment_column,"outcome_column":outcome_column,"treatment_value":treatment_value,"treated_count":len(treated),"control_count":len(control),"treated_mean":tm,"control_mean":cm,"difference_in_means":tm-cm,"cohens_d":(tm-cm)/pooled if pooled else None,"warning":"association only; confounding and selection effects are not controlled"}

def _domain_test(rows:list[dict[str,Any]],column:str,test:str,alpha:float)->dict[str,Any]:
    values=[_number(row.get(column)) for row in rows]; values=[float(value) for value in values if value is not None]
    if len(values)<3:return {"status":"insufficient_data","test":test,"column":column,"count":len(values)}
    normalized=test.strip().lower()
    if normalized in {"normality","jarque_bera","jarque-bera"}:
        mean=statistics.fmean(values); sd=statistics.pstdev(values)
        if sd==0:return {"status":"degenerate","test":"jarque_bera","column":column,"count":len(values),"statistic":0.0,"p_value":1.0,"alpha":alpha}
        skew=statistics.fmean([((v-mean)/sd)**3 for v in values]); kurt=statistics.fmean([((v-mean)/sd)**4 for v in values])-3
        statistic=len(values)/6*(skew*skew+(kurt*kurt)/4); p_value=math.erfc(math.sqrt(max(0.0,statistic)/2))
        return {"status":"ok","test":"jarque_bera","column":column,"count":len(values),"statistic":statistic,"p_value_approx":p_value,"alpha":alpha,"reject_normality":p_value<alpha,"warning":"chi-square approximation; use a domain package for confirmatory inference"}
    if normalized in {"zscore","outlier_zscore"}:
        mean=statistics.fmean(values); sd=statistics.pstdev(values); threshold=3.0
        outliers=[] if sd==0 else [{"row_index":index,"z":(value-mean)/sd,"value":value} for index,value in enumerate(values) if abs((value-mean)/sd)>threshold]
        return {"status":"ok","test":"zscore","column":column,"count":len(values),"mean":mean,"stddev_population":sd,"threshold":threshold,"outliers":outliers}
    raise ResearchAnalysisError(f"unsupported domain test: {test}")

def analyze_tabular(rows:Iterable[Mapping[str,Any]],*,numeric_columns:Iterable[str],group_by:str|None=None,time_column:str|None=None,evidence_ids:Iterable[str]=(),forecast_columns:Iterable[str]=(),forecast_horizon:int=0,treatment_column:str|None=None,outcome_column:str|None=None,treatment_value:Any=None,domain_test:str|None=None,domain_column:str|None=None,alpha:float=.05,max_rows:int=10_000)->dict[str,Any]:
    data=[dict(row) for row in rows]
    if not data:raise ResearchAnalysisError("analysis requires at least one row")
    if len(data)>max_rows:raise ResearchAnalysisError(f"analysis exceeds {max_rows} rows")
    columns=sorted({str(key) for row in data for key in row})
    if len(columns)>100:raise ResearchAnalysisError("analysis exceeds 100 columns")
    numeric=list(dict.fromkeys(str(item).strip() for item in numeric_columns if str(item).strip()))
    if not numeric:raise ResearchAnalysisError("at least one numeric column is required")
    missing=[item for item in numeric if item not in columns]
    if missing:raise ResearchAnalysisError(f"missing numeric columns: {', '.join(missing)}")
    if group_by and group_by not in columns:raise ResearchAnalysisError(f"missing group column: {group_by}")
    if time_column and time_column not in columns:raise ResearchAnalysisError(f"missing time column: {time_column}")
    descriptive={column:_describe(data,column) for column in numeric}
    groups={}
    if group_by:
        buckets=defaultdict(list)
        for row in data:buckets[str(row.get(group_by))].append(row)
        if len(buckets)>200:raise ResearchAnalysisError("analysis exceeds 200 groups")
        groups={key:{column:_describe(bucket,column) for column in numeric} for key,bucket in sorted(buckets.items())}
    correlations=[_correlation(data,left,right) for i,left in enumerate(numeric) for right in numeric[i+1:]]
    trends={}
    if time_column:
        dated = sorted([(stamp, row) for row in data if (stamp:=_time(row.get(time_column))) is not None], key=lambda item: item[0])
        for column in numeric:
            series=[(stamp,value) for stamp,row in dated if (value:=_number(row.get(column))) is not None]
            if len(series)>=2:
                start,end=series[0],series[-1];change=end[1]-start[1]
                trends[column]={"count":len(series),"start_time":start[0].isoformat(),"end_time":end[0].isoformat(),"start":start[1],"end":end[1],"absolute_change":change,"percent_change":change/start[1]*100 if start[1] else None}
            else:trends[column]={"count":len(series),"reason":"fewer_than_two_dated_values"}
    outliers={}
    for column in numeric:
        d=descriptive[column];iqr=d["q3"]-d["q1"];low=d["q1"]-1.5*iqr;high=d["q3"]+1.5*iqr;found=[{"row_index":i,"value":value} for i,row in enumerate(data) if (value:=_number(row.get(column))) is not None and (value<low or value>high)]
        outliers[column]={"method":"iqr_1.5","lower_bound":low,"upper_bound":high,"rows":found[:100],"truncated":len(found)>100}
    forecasts={}
    requested_forecasts=list(dict.fromkeys(str(item).strip() for item in forecast_columns if str(item).strip()))
    if requested_forecasts:
        if not time_column: raise ResearchAnalysisError("forecasting requires time_column")
        forecasts={column:_linear_forecast(data,time_column,column,int(forecast_horizon or 3)) for column in requested_forecasts}
    causal=None
    if treatment_column or outcome_column:
        if not treatment_column or not outcome_column: raise ResearchAnalysisError("causal analysis requires treatment_column and outcome_column")
        causal=_observational_effect(data,treatment_column,outcome_column,treatment_value)
    domain_tests={}
    if domain_test:
        if not domain_column: raise ResearchAnalysisError("domain_test requires domain_column")
        domain_tests[domain_column]=_domain_test(data,domain_column,domain_test,float(alpha))
    canonical=json.dumps(data,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
    return {"schema_version":2,"row_count":len(data),"columns":columns,"dataset_sha256":hashlib.sha256(canonical).hexdigest(),"evidence_ids":list(dict.fromkeys(str(item) for item in evidence_ids)),"missing_policy":"exclude_per_metric_no_imputation","descriptive":descriptive,"groups":groups,"correlations":correlations,"trends":trends,"outliers":outliers,"forecasts":forecasts,"causal":causal,"domain_tests":domain_tests,"methods_disclaimer":"Forecasts are bounded extrapolations; causal output is observational association and not causal; domain tests are screening diagnostics unless independently validated."}
