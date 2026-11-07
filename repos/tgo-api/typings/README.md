# API 第三方 SDK 类型声明

这里只声明本服务实际使用的 SDK 接口，不替代 SDK，不实现网络请求，也不声称覆盖上游的全部功能。Mypy 从 `pyproject.toml` 的 `$MYPY_CONFIG_FILE_DIR/typings` 读取这些声明；该路径不依赖执行命令时的当前目录。

| 目录 | 核对的 SDK 版本 | 本项目使用的范围 |
| --- | --- | --- |
| `ip2region` | `py-ip2region 3.0.2` | `Version` 构造参数、IPv4/IPv6 版本对象、文件搜索器、查询和关闭 |
| `oss2` | `oss2 2.19.1` | `Auth`、`Bucket` 的基础读写/删除/存在检查，以及 `models.GetObjectResult` 的长度、读取和关闭 |

声明依据是对应 SDK 的 `util.py`、`searcher.py`、`api.py`、`auth.py` 和 `models.py`。这些源码随开发环境中安装的包提供。增加调用方式、升级 SDK 或调整返回结构时，应同步核对声明与实际源码。

S3 使用已发布的 [boto3-stubs](https://pypi.org/project/boto3-stubs/1.42.24/)、`botocore-stubs` 和 `mypy-boto3-s3` 开发依赖，不在本目录重复定义。S3 服务声明固定为 `1.42.21`，与已锁定 `boto3/botocore 1.42.24` 的调用范围一并验证；不得仅因同属 `1.42` 就假定任意后续声明都适用。

在 `repos/tgo-api` 下执行：

```powershell
.venv/Scripts/python.exe -m mypy app
.venv/Scripts/python.exe -m pytest -q -o 'addopts=--strict-markers --strict-config' tests/test_ip2region_sdk_contract.py tests/test_optional_storage_sdks.py tests/test_storage_sdk_contract.py tests/test_cloud_media_read.py tests/test_storage_configuration.py tests/test_local_storage_urls.py
```

SDK 契约测试使用实际 SDK、合成 XDB、S3 Stubber 和 OSS 响应对象，不连接真实存储桶或定位数据库。其通过不等于云端 Provider 验收。完整 Mypy 仍可能报告其他模块的错误，不应因此忽略导入、放宽 SDK 参数类型或删除回归断言。
