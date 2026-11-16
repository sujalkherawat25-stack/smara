"""GLM sometimes leaks its XML tool-call format into JSON arguments. Seen in production on 12 and 15 Nov: every scheduled
reminder's first send failed with 'unknown obligation_id' because party_id had been glued into the obligation_id value."""
import json

from src.loop.llm import repair_args, repair_call

LEAK = ('{"obligation_id":"d239581e-a1dd-4358-a47e-896337790584</arg_value><arg_key>party_id</arg_key>'
        '<arg_value>4e20a887-640b-4654-898d-4572a95d6916","text":"Namaste ji"}')


def test_leaked_key_is_split_back_out():
    a = json.loads(repair_args(LEAK))
    assert a == {"obligation_id": "d239581e-a1dd-4358-a47e-896337790584",
                 "party_id": "4e20a887-640b-4654-898d-4572a95d6916", "text": "Namaste ji"}


def test_several_leaked_keys_and_a_trailing_close_tag():
    raw = json.dumps({"to": "p1</arg_value><arg_key>obligation_id</arg_key><arg_value>o1</arg_value><arg_key>text</arg_key><arg_value>hi</arg_value>"})
    assert json.loads(repair_args(raw)) == {"to": "p1", "obligation_id": "o1", "text": "hi"}


def test_explicit_key_wins_over_a_leaked_one_in_any_order():
    raw = json.dumps({"to": "real", "party_id": "x</arg_value><arg_key>to</arg_key><arg_value>leaked"})
    assert json.loads(repair_args(raw))["to"] == "real"
    raw = json.dumps({"party_id": "x</arg_value><arg_key>to</arg_key><arg_value>leaked", "to": "real"})
    assert json.loads(repair_args(raw))["to"] == "real"


def test_pure_xml_arguments_are_parsed():
    raw = "<arg_key>query</arg_key><arg_value>rice price</arg_value><arg_key>n</arg_key><arg_value>3</arg_value>"
    assert json.loads(repair_args(raw)) == {"query": "rice price", "n": "3"}


def test_clean_arguments_are_untouched():
    for raw in ['{"text": "arg_value is a word here"}', '{"a": 1}', "", None, "[1,2]"]:
        assert repair_args(raw) == raw


def test_repair_call_keeps_the_call_shape():
    call = {"id": "c1", "type": "function", "function": {"name": "send_whatsapp_message", "arguments": LEAK}}
    fixed = repair_call(call)
    assert fixed["id"] == "c1" and fixed["function"]["name"] == "send_whatsapp_message"
    assert json.loads(fixed["function"]["arguments"])["party_id"].startswith("4e20a887")
    clean = {"id": "c2", "function": {"name": "x", "arguments": "{}"}}
    assert repair_call(clean) is clean
