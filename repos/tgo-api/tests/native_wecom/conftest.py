import os

os.environ.setdefault("SECRET_KEY", "native-wecom-test-secret-not-a-real-credential")
os.environ.setdefault(
    "DATABASE_URL", "postgresql://test:test@127.0.0.1/native_wecom_test"
)
