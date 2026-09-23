"""Statement totals and field selection must not confuse net settlement with sale price."""

import pytest

from app.services.wechat_bill_parser import fen, parse_trade_bill


def statement(row="`synthetic-order,`synthetic-tx,`123,`wx-synthetic,`SUCCESS,`CNY,`29.99,`27.00", count="1"):
    return ("商户订单号,微信订单号,商户号,公众账号ID,交易状态,货币种类,订单金额,应结订单金额\n"
            + row + "\n总交易单数,订单总金额\n`" + count + ",`29.99\n").encode()


def test_statement_uses_order_amount_not_net_settlement():
    rows = parse_trade_bill(statement())
    assert len(rows) == 1 and rows[0].amount == 2999
    assert rows[0].number == "synthetic-order" and rows[0].refund_number is None


def test_truncated_or_wrong_count_statement_is_rejected():
    with pytest.raises(ValueError):
        parse_trade_bill(statement(count="2"))
    with pytest.raises(ValueError):
        parse_trade_bill(statement().split(b"\n", 2)[0])


@pytest.mark.parametrize("value", ["1.001", "nan", "1e3", "-1", ""])
def test_money_is_exact_and_nonnegative(value):
    with pytest.raises(ValueError):
        fen(value)
