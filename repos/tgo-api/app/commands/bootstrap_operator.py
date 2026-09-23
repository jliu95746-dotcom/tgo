"""Create an operator with interactive, non-echoing password input."""

import argparse
from getpass import getpass

from pydantic import SecretStr, ValidationError


def main() -> int:
    parser = argparse.ArgumentParser(description="创建域见平台运营账号（不会提升企业管理员权限）")
    parser.add_argument("--email", required=True, help="运营人员邮箱")
    parser.add_argument("--name", required=True, help="运营人员名称")
    args = parser.parse_args()

    from app.core.database import SessionLocal
    from app.core.exceptions import TGOAPIException
    from app.schemas.operations import OperatorCreateRequest
    from app.services.operations_auth import create_operator

    password = getpass("请输入运营账号密码（至少 12 位，不回显）：")
    confirmation = getpass("请再次输入密码：")
    if password != confirmation:
        parser.exit(1, "两次密码不一致，未创建账号。\n")
    try:
        data = OperatorCreateRequest(
            email=args.email,
            name=args.name,
            password=SecretStr(password),
        )
    except ValidationError:
        parser.exit(1, "请检查邮箱、名称及密码长度（至少 12 位且不超过 72 个 UTF-8 字节）。\n")
    try:
        with SessionLocal() as db:
            create_operator(db, data)
    except TGOAPIException as exc:
        parser.exit(1, exc.message + "\n")
    except Exception:
        parser.exit(1, "创建失败，请核对数据库连接和运营账号迁移；未输出连接信息。\n")
    parser.exit(0, "域见运营账号已创建。请确保运营开关及独立签名密钥已配置后登录。\n")


if __name__ == "__main__":
    raise SystemExit(main())
