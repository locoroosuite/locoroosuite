import os
import smtplib
import ssl


def _get_ssl_context():
    if os.environ.get("APP_ENV", "development") == "development":
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return None


def smtp_connect(host, port, tls_mode="starttls"):
    ctx = _get_ssl_context()
    if tls_mode == "smtps":
        server = smtplib.SMTP_SSL(host, port, context=ctx)
    else:
        server = smtplib.SMTP(host, port)
        server.ehlo()
        server.starttls(context=ctx)
    server.ehlo()
    return server


def smtp_login(server, username, password=None):
    server.login(username, password)


def smtp_send(server, from_addr, to_addrs, msg_bytes):
    server.sendmail(from_addr, to_addrs, msg_bytes)
