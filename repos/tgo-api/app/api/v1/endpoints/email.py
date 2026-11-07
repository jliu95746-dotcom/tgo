"""Email service endpoints."""

import imaplib
import logging
import smtplib
import socket
from typing import Dict, Any

from fastapi import APIRouter, Depends, status

from app.core.logging import get_logger
from app.core.security import get_authenticated_project
from app.core.security import AuthenticatedProject
from app.schemas.email_schema import (
    EmailConnectionTestRequest,
    EmailConnectionTestResponse,
)

logger = get_logger("endpoints.email")
router = APIRouter()


def test_smtp_connection(
    host: str,
    port: int,
    username: str,
    password: str,
    use_tls: bool,
    timeout: int = 30,
) -> Dict[str, str]:
    (
        '\n'
        '    Test SMTP server connection.\n'
        '    \n'
        '    Args:\n'
        '        host: SMTP server hostname\n'
        '        port: SMTP server port\n'
        '        username: SMTP username\n'
        '        password: SMTP password\n'
        '        use_tls: Whether to use TLS/STARTTLS\n'
        '        timeout: Connection timeout in seconds\n'
        '        \n'
        '    Returns:\n'
        "        Dict with 'status' and 'message' keys\n"
        '    '
    )
    try:
        logger.info(
            f"Testing SMTP connection to {host}:{port} (TLS: {use_tls})"
        )

        # Create SMTP connection based on TLS setting
        if use_tls:
            # For ports 587 and 25, use STARTTLS
            server = smtplib.SMTP(host, port, timeout=timeout)
            server.ehlo()
            server.starttls()
            server.ehlo()
        else:
            # For port 465, use SMTP_SSL
            server = smtplib.SMTP_SSL(host, port, timeout=timeout)

        # Attempt login
        server.login(username, password)

        # Close connection
        server.quit()

        logger.info(f"SMTP connection to {host}:{port} successful")
        return {
            "status": "success",
            "message": f"SMTP connection successful to {host}:{port}",
        }

    except smtplib.SMTPAuthenticationError as e:
        error_msg = f"SMTP authentication failed: {str(e)}"
        logger.warning(error_msg)
        return {"status": "failed", "message": error_msg}

    except smtplib.SMTPConnectError as e:
        error_msg = f"SMTP connection error: {str(e)}"
        logger.warning(error_msg)
        return {"status": "failed", "message": error_msg}

    except socket.timeout:
        error_msg = f"SMTP connection timeout to {host}:{port}"
        logger.warning(error_msg)
        return {"status": "failed", "message": error_msg}

    except socket.gaierror as e:
        error_msg = f"SMTP hostname resolution failed: {str(e)}"
        logger.warning(error_msg)
        return {"status": "failed", "message": error_msg}

    except Exception as e:
        error_msg = f"SMTP connection failed: {type(e).__name__}: {str(e)}"
        logger.error(error_msg)
        return {"status": "failed", "message": error_msg}


def test_imap_connection(
    host: str,
    port: int,
    username: str,
    password: str,
    use_ssl: bool,
    timeout: int = 30,
) -> Dict[str, str]:
    (
        '\n'
        '    Test IMAP server connection.\n'
        '    \n'
        '    Args:\n'
        '        host: IMAP server hostname\n'
        '        port: IMAP server port\n'
        '        username: IMAP username\n'
        '        password: IMAP password\n'
        '        use_ssl: Whether to use SSL\n'
        '        timeout: Connection timeout in seconds\n'
        '        \n'
        '    Returns:\n'
        "        Dict with 'status' and 'message' keys\n"
        '    '
    )
    try:
        logger.info(
            f"Testing IMAP connection to {host}:{port} (SSL: {use_ssl})"
        )

        # Create IMAP connection based on SSL setting
        mail: imaplib.IMAP4
        if use_ssl:
            mail = imaplib.IMAP4_SSL(host, port, timeout=timeout)
        else:
            mail = imaplib.IMAP4(host, port, timeout=timeout)

        # Attempt login
        mail.login(username, password)

        # Close connection
        mail.logout()

        logger.info(f"IMAP connection to {host}:{port} successful")
        return {
            "status": "success",
            "message": f"IMAP connection successful to {host}:{port}",
        }

    except imaplib.IMAP4.error as e:
        error_msg = f"IMAP authentication or protocol error: {str(e)}"
        logger.warning(error_msg)
        return {"status": "failed", "message": error_msg}

    except socket.timeout:
        error_msg = f"IMAP connection timeout to {host}:{port}"
        logger.warning(error_msg)
        return {"status": "failed", "message": error_msg}

    except socket.gaierror as e:
        error_msg = f"IMAP hostname resolution failed: {str(e)}"
        logger.warning(error_msg)
        return {"status": "failed", "message": error_msg}

    except Exception as e:
        error_msg = f"IMAP connection failed: {type(e).__name__}: {str(e)}"
        logger.error(error_msg)
        return {"status": "failed", "message": error_msg}


@router.post(
    "/test-connection",
    response_model=EmailConnectionTestResponse,
    status_code=status.HTTP_200_OK,
    summary="Test Email Server Connection",
    description=(
        '\n'
        '    Test SMTP and IMAP server connections with the provided '
        'credentials.\n'
        '\n'
        '    This endpoint validates email server configurations by '
        'attempting to:\n'
        '    1. Connect to the SMTP server and authenticate\n'
        '    2. Connect to the IMAP server and authenticate\n'
        '\n'
        '    Both tests are performed independently, and detailed results '
        'are returned for each.\n'
        '\n'
        '    **Security Notes:**\n'
        '    - Credentials are not stored or logged\n'
        '    - Connection timeout is set to 30 seconds\n'
        '    - This endpoint requires authentication\n'
        '\n'
        '    **Common Port Configurations:**\n'
        '    - SMTP: 587 (TLS), 465 (SSL), 25 (TLS)\n'
        '    - IMAP: 993 (SSL), 143 (no SSL)\n'
        '    '
    ),
)
async def test_email_connection(
    request: EmailConnectionTestRequest,
    project_and_api_key: AuthenticatedProject = Depends(
        get_authenticated_project
    ),
) -> EmailConnectionTestResponse:
    """
    Test email server connection for SMTP and IMAP.

    This endpoint helps users validate their email server configurations
    before saving them to the system.
    """
    project, _ = project_and_api_key

    logger.info(
        f"Testing email connection for project {project.id}",
        extra={
            "project_id": str(project.id),
            "smtp_host": request.smtp_host,
            "smtp_port": request.smtp_port,
            "imap_host": request.imap_host,
            "imap_port": request.imap_port,
            # DO NOT log passwords
        },
    )

    # Test SMTP connection
    smtp_result = test_smtp_connection(
        host=request.smtp_host,
        port=request.smtp_port,
        username=request.smtp_username,
        password=request.smtp_password,
        use_tls=request.smtp_use_tls,
        timeout=30,
    )

    # Test IMAP connection
    imap_result = test_imap_connection(
        host=request.imap_host,
        port=request.imap_port,
        username=request.imap_username,
        password=request.imap_password,
        use_ssl=request.imap_use_ssl,
        timeout=30,
    )

    # Determine overall success
    overall_success = (
        smtp_result["status"] == "success"
        and imap_result["status"] == "success"
    )

    logger.info(
        f"Email connection test completed for project {project.id}",
        extra={
            "project_id": str(project.id),
            "smtp_status": smtp_result["status"],
            "imap_status": imap_result["status"],
            "overall_success": overall_success,
        },
    )

    return EmailConnectionTestResponse(
        smtp_status=smtp_result["status"],
        smtp_message=smtp_result["message"],
        imap_status=imap_result["status"],
        imap_message=imap_result["message"],
        overall_success=overall_success,
    )
