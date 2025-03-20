from typing import Any

from dify_plugin import ToolProvider
from dify_plugin.errors.tool import ToolProviderCredentialValidationError
from urllib.parse import urlparse, parse_qs
import pymysql

class RookieText2dataProvider(ToolProvider):
    def _validate_credentials(self, credentials: dict[str, Any]) -> None:
        try:
            # 参数校验
            if not credentials.get('db_url') or not credentials.get('db_password'):
                raise ValueError("Database URL and password are required")
            
            # 解析数据库URL（支持复杂格式）
            db_url = credentials['db_url']
            parsed = urlparse(db_url)
            
            # 验证协议
            if parsed.scheme != 'mysql':
                raise ValueError("Only mysql:// scheme is supported")

            # 提取连接参数（带智能默认值）
            username = parsed.username or 'root'
            password = credentials['db_password']
            host = parsed.hostname or 'localhost'
            port = parsed.port or 3306
            database = parsed.path.lstrip('/') or 'test'

            # 提取额外参数（如SSL配置）
            query_params = parse_qs(parsed.query)
            ssl_mode = query_params.get('ssl_mode', ['DISABLED'])[0]

            # 建立数据库连接（包含SSL配置）
            conn = pymysql.connect(
                host=host,
                port=port,
                user=username,
                password=password,
                database=database,
                ssl={'ssl': None} if ssl_mode == 'DISABLED' else None,
                connect_timeout=5,
            )
            # 验证连接有效性
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
            conn.close()

        except ValueError as ve:
            raise ToolProviderCredentialValidationError(f"配置错误: {str(ve)}")
        except pymysql.OperationalError as oe:
            error_msg = f"连接失败: {str(oe)}"
            if "caching_sha2_password" in str(oe):
                error_msg += "\n请确认已安装：pip install pymysql[cryptography]"
            raise ToolProviderCredentialValidationError(error_msg)
        except Exception as e:
            raise ToolProviderCredentialValidationError(f"未知错误: {str(e)}")
