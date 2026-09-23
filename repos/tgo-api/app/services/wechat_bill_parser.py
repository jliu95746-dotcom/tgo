"""Parse verified ALL statements using provider column names and exact fen."""

import csv
import io
import re
from decimal import Decimal

from pydantic import BaseModel


class TradeBillEntry(BaseModel):
    number: str
    transaction_id: str
    merchant_id: str
    app_id: str
    state: str
    currency: str
    amount: int
    refund_number: str | None


def fen(value: str) -> int:
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", value):
        raise ValueError("Invalid statement amount")
    return int(Decimal(value) * 100)


def parse_trade_bill(content: bytes) -> list[TradeBillEntry]:
    rows = iter(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
    headers = next(rows, [])
    if len(set(headers)) != len(headers) or not {
        "商户订单号", "微信订单号", "商户号", "公众账号ID", "交易状态", "货币种类", "订单金额"
    }.issubset(headers):
        raise ValueError("Unsupported statement columns; operator review required")
    entries: list[TradeBillEntry] = []
    summary_count: int | None = None
    for cells in rows:
        if not cells:
            continue
        if cells[0] == "总交易单数":
            summary = next(rows, [])
            if not summary:
                raise ValueError("Missing statement summary")
            summary_count = int(summary[0].removeprefix("`"))
            break
        if len(cells) != len(headers):
            raise ValueError("Invalid statement row width")
        values = dict(zip(headers, (cell.removeprefix("`") for cell in cells)))
        refund = values.get("商户退款单号", "")
        entries.append(TradeBillEntry(number=values["商户订单号"], transaction_id=values["微信订单号"],
            merchant_id=values["商户号"], app_id=values["公众账号ID"], state=values["交易状态"],
            currency=values["货币种类"], amount=fen(values["订单金额"]),
            refund_number=refund if refund and refund != "0" else None))
    if summary_count is None or summary_count != len(entries):
        raise ValueError("Statement count does not match its summary")
    return entries
