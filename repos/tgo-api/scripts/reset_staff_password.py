"""Interactive local-operator recovery; passwords are never command arguments/output."""

import argparse
import getpass
import sys
import warnings
from collections.abc import Callable
from pathlib import Path

from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal  # noqa: E402
from app.services.password_recovery import (  # noqa: E402
    find_recovery_target,
    reset_confirmed_password,
)


def run_recovery(
    db: Session,
    prompt: Callable[[str], str] = input,
    secret_prompt: Callable[[str], str] = getpass.getpass,
    write: Callable[[str], None] = print,
) -> bool:
    write("仅供部署管理员使用；请先核实账号持有人的身份。")
    target = find_recovery_target(db, prompt("完整邮箱或用户名："))
    write(f"账号：{target.username}\n项目：{target.project_name}\n项目 ID：{target.project_id}")
    confirmation = prompt("核对后输入完整项目 ID 确认（直接回车取消）：").strip()
    if confirmation != str(target.project_id):
        write("已取消，未修改密码。")
        return False
    # Refuse getpass's visible-input fallback when terminal privacy is unavailable.
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        password = secret_prompt("新密码（输入不显示）：")
        repeated = secret_prompt("再次输入新密码：")
    if password != repeated:
        raise ValueError("两次密码不一致，未修改密码。")
    reset_confirmed_password(db, target.staff_id, target.project_id, password)
    write("密码已更新。请使用新密码登录，并通过安全渠道交付给账号持有人。")
    write("注意：本操作不撤销此前已签发的登录令牌，不可代替失窃账号的会话处置。")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="本机交互式账号密码恢复（不会显示密码）")
    parser.parse_args()
    if not sys.stdin.isatty():
        print("请在部署主机的交互式终端中运行，不支持管道或后台输入密码。")
        return 2
    try:
        with SessionLocal() as db:
            run_recovery(db)
        return 0
    except (EOFError, KeyboardInterrupt):
        print("已取消，未完成密码恢复。")
        return 1
    except (ValueError, getpass.GetPassWarning) as exc:
        print(str(exc))
        return 1
    except Exception as exc:
        # Keep SQL, connection strings, credentials and exception text private.
        print(f"密码恢复失败（{type(exc).__name__}），请联系部署管理员检查服务。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
