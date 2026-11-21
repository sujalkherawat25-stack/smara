"""calculate: exact arithmetic for money questions, so the agent does not work a multi-step sum out in its head.

A fast model that thinks briefly gets a sum like 'Rs 84,500, 2.5% discount, then 5% GST' wrong now and then (86,500.31 for 86,506.88), and an
accountant stops trusting every figure after one slip. The tool takes ONE expression, evaluates it with exact decimals (never floats), and returns
the result at full precision and rounded. It parses the expression itself: numbers, + - * / ** %, brackets; nothing else is ever executed.
"""
import ast
import re
from decimal import ROUND_HALF_UP, Decimal, DivisionByZero, InvalidOperation

from src.agent.ca_reports import rs

MAX_CHARS = 300
MAX_NODES = 80
BINARY = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b, ast.Mod: lambda a, b: a % b}


def clean(expression: str) -> str:
    """'84,500 x (1 - 2.5%) x 1.05' -> '84500*(1-(2.5/100))*1.05': thousands commas, Rs, x or the multiplication sign, ^ and a percent sign are accepted."""
    e = str(expression or "").strip().replace("×", "*").replace("÷", "/").replace("^", "**").replace("₹", "")
    e = re.sub(r"(?i)\brs\.?|\binr\b", "", e)
    e = re.sub(r"(?<=[\d)])\s*[xX]\s*(?=[\d(])", "*", e)
    e = e.replace(",", "").replace("_", "")
    e = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"(\1/100)", e)
    return re.sub(r"\s+", "", e)


def _eval(node, count: list) -> Decimal:
    count[0] += 1
    if count[0] > MAX_NODES:
        raise ValueError("the expression is too long")
    if isinstance(node, ast.Expression):
        return _eval(node.body, count)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return Decimal(str(node.value))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        v = _eval(node.operand, count)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Pow):
            base, power = _eval(node.left, count), _eval(node.right, count)
            if power != power.to_integral_value() or not (-10 <= power <= 10):
                raise ValueError("a power must be a whole number from -10 to 10")
            return base ** int(power)
        op = BINARY.get(type(node.op))
        if op:
            return op(_eval(node.left, count), _eval(node.right, count))
    raise ValueError("only numbers, + - * / ** %, and brackets are allowed")


def evaluate(expression: str) -> Decimal:
    e = clean(expression)
    if not e or len(e) > MAX_CHARS:
        raise ValueError("give one arithmetic expression, for example 84500*(1-2.5%)*(1+5%)")
    try:
        return _eval(ast.parse(e, mode="eval"), [0])
    except SyntaxError:
        raise ValueError(f"could not read the expression '{e[:80]}'")
    except (DivisionByZero, InvalidOperation):
        raise ValueError("division by zero, or a number that is too large")


def plain(d: Decimal) -> str:
    s = format(d.normalize(), "f") if d == d.to_integral_value() or abs(d) < Decimal("1e15") else str(d)
    return s


async def calculate(app, ctx, args: dict) -> dict:
    try:
        value = evaluate(args.get("expression"))
    except ValueError as e:
        return {"error": str(e)}
    places = args.get("round_to")
    q2 = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    whole = value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    out = {"expression": clean(args.get("expression")), "result": plain(value), "to_2_decimals": str(q2), "to_whole_rupees": str(whole),
           "in_rupees": rs(float(q2))}
    if isinstance(places, int) and 0 <= places <= 6:
        out["rounded"] = str(value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))
    out["next"] = "Quote this result in your answer; never recompute it. Say what the figure is (for example 'after 2.5% discount and 5% GST')."
    return out
