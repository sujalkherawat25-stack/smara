from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any,Mapping,Sequence


@dataclass(frozen=True)
class ValidationResult:
    passed:bool
    reason:str
    details:Mapping[str,Any]


def validate_json(path:Path,*,required_fields:Sequence[str]=(),expected:Mapping[str,Any]|None=None)->ValidationResult:
    try:value=json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:return ValidationResult(False,"invalid_json",{"error":str(exc)})
    if not isinstance(value,dict):return ValidationResult(False,"json_root_not_object",{})
    missing=[field for field in required_fields if field not in value]
    if missing:return ValidationResult(False,"missing_fields",{"fields":missing})
    wrong={key:{"expected":wanted,"actual":value.get(key)} for key,wanted in dict(expected or {}).items() if value.get(key)!=wanted}
    if wrong:return ValidationResult(False,"wrong_values",wrong)
    return ValidationResult(True,"valid",{"fields":sorted(value)})


def validate_csv(path:Path,*,required_columns:Sequence[str]=(),expected_rows:Sequence[Mapping[str,str]]|None=None)->ValidationResult:
    try:
        with Path(path).open(encoding="utf-8-sig",newline="") as handle:
            reader=csv.DictReader(handle);rows=list(reader);columns=list(reader.fieldnames or [])
    except Exception as exc:return ValidationResult(False,"invalid_csv",{"error":str(exc)})
    missing=[column for column in required_columns if column not in columns]
    if missing:return ValidationResult(False,"missing_columns",{"columns":missing})
    expected=[dict(row) for row in expected_rows or ()]
    if expected_rows is not None and rows!=expected:return ValidationResult(False,"wrong_rows",{"expected":expected,"actual":rows})
    return ValidationResult(True,"valid",{"columns":columns,"row_count":len(rows)})


def validate_report(path:Path,*,required_phrases:Sequence[str]=(),forbidden_phrases:Sequence[str]=(),minimum_words:int=1)->ValidationResult:
    try:text=Path(path).read_text(encoding="utf-8")
    except Exception as exc:return ValidationResult(False,"unreadable_report",{"error":str(exc)})
    missing=[phrase for phrase in required_phrases if phrase not in text]
    forbidden=[phrase for phrase in forbidden_phrases if phrase in text]
    if missing:return ValidationResult(False,"missing_content",{"phrases":missing})
    if forbidden:return ValidationResult(False,"forbidden_content",{"phrases":forbidden})
    words=text.split()
    if len(words)<minimum_words:return ValidationResult(False,"report_too_short",{"word_count":len(words)})
    return ValidationResult(True,"valid",{"word_count":len(words)})
