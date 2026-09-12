"""Deterministic, provenance-bound analysis for research datasets."""
from __future__ import annotations
import hashlib,json,math,statistics
from collections import defaultdict
from datetime import datetime
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

def analyze_tabular(rows:Iterable[Mapping[str,Any]],*,numeric_columns:Iterable[str],group_by:str|None=None,time_column:str|None=None,evidence_ids:Iterable[str]=(),max_rows:int=10_000)->dict[str,Any]:
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
        dated=sorted((stamp,row) for row in data if (stamp:=_time(row.get(time_column))) is not None)
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
    canonical=json.dumps(data,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
    return {"schema_version":1,"row_count":len(data),"columns":columns,"dataset_sha256":hashlib.sha256(canonical).hexdigest(),"evidence_ids":list(dict.fromkeys(str(item) for item in evidence_ids)),"missing_policy":"exclude_per_metric_no_imputation","descriptive":descriptive,"groups":groups,"correlations":correlations,"trends":trends,"outliers":outliers}
