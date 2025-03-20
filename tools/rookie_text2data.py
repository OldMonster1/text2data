from collections.abc import Generator
from typing import Any
from urllib.parse import urlparse, urlunparse, quote, unquote
import pymysql
from dify_plugin import Tool
from dify_plugin.entities.model.llm import LLMModelConfig
from dify_plugin.entities.tool import ToolInvokeMessage
from dify_plugin.entities.model.message import SystemPromptMessage, UserPromptMessage
from pymysql.cursors import DictCursor

class RookieText2dataTool(Tool):
    
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        model_info= tool_parameters.get('model')
        # 获取连接参数
        _, conn_params = self._build_mysql_dsn(
            self.runtime.credentials['db_url'],
            self.runtime.credentials['db_password']
        )
        # 获取元数据
        metadata = self._get_metadata(conn_params)

        response = self.session.model.llm.invoke(
            model_config=LLMModelConfig(
                provider=model_info.get('provider'),
                model=model_info.get('model'),
                mode=model_info.get('mode'),
                completion_params=model_info.get('completion_params')
            ),
            prompt_messages=[
                SystemPromptMessage(
                    content=f"""你是一位资深数据库工程师兼SQL优化专家，拥有10年以上DBA经验。请根据提供的数据库元数据DDL和自然语言需求描述，生成符合企业级标准的优化SQL语句。

                            ## 系统要求：
                            1. 必须严格嵌入提供的DDL元数据{metadata}，禁止使用任何未声明的表或字段
                            2. 仅返回SELECT语句，禁止包含INSERT/UPDATE/DELETE等DML操作
                            3. 必须使用LIMIT语句进行结果限制，防止数据泄露风险
                            5. 如果用户提出了具体的数据数量，则Limit用户的查询数量，否则Limit 100
                            4. 所有字段必须使用反引号包裹，符合MySQL标识符规范

                            ## 优化原则：
                            1. 采用覆盖索引策略，确保查询命中至少2个索引
                            2. 避免SELECT *，仅返回需求中的必要字段
                            3. 对日期字段使用CURDATE()等函数时需标注时间范围
                            4. 多表关联必须使用INNER JOIN，禁止LEFT/RIGHT JOIN

                            ## 验证机制：
                            1. 生成后自动检查表是否存在，若不存在则抛出错误
                            2. 条件字段值必须存在于目标表中，否则提示字段不存在
                            3. 自动生成EXPLAIN计划，确保type列显示为ref或eq_ref
                            4. 仅返回生成的SQL语句，禁止返回注释、DDL、描述等与SQL无关内容
                            5. 禁止使用任何转义字符（如''或\"）
                            6. 禁止在开头和结尾使用``` ```包裹SQL语句

                            ## 输出规范：
                            SELECT 
                                *
                            FROM 
                                `xsdd` o
                            INNER JOIN 
                                `ggxs` c ON o.customer_id = c.id
                            WHERE 
                                o.`客户编码` = '2414A' 
                                AND c.region = 'Asia'
                                AND o.`日期` BETWEEN '2025-3-15' AND CURDATE()
                            LIMIT 100;
                    """
                ),
                UserPromptMessage(
                    content=f"用户想要查询的数据需求为：{tool_parameters.get('query')}"
                    
                )
            ],
            stream=False
        )

        excute_sql = response.message.content

        print(excute_sql)

        # 执行SQL
        result = self._execute_sql_generator(excute_sql, conn_params)

        yield self.create_json_message({
            "status": "success",
            "data": result
        })

    def _build_mysql_dsn(self, db_url: str, db_password: str) -> tuple[str, dict[str, Any]]:
        """
        将数据库URL和密码拼接为完整DSN，并返回解析后的连接参数
        
        参数：
            db_url (str): 格式示例 mysql://user@host:port/database
            db_password (str): 数据库密码（明文）
        
        返回：
            tuple: (完整DSN, 解析后的连接参数字典)
        
        异常：
            ValueError: 当URL格式无效时抛出
        """
        # 基础解析验证
        parsed = urlparse(db_url)
        if parsed.scheme != 'mysql':
            raise ValueError("仅支持mysql协议，当前协议：{}".format(parsed.scheme))

        # 解析用户名和主机信息
        username = parsed.username or 'root'
        password = quote(db_password, safe='')  # 处理特殊字符
        hostname = parsed.hostname or 'localhost'
        port = parsed.port or 3306

        # 处理数据库路径
        database = parsed.path.lstrip('/')
        if not database:
            database = 'test'

        # 构建新的netloc
        auth_part = f"{username}:{password}"
        netloc = f"{auth_part}@{hostname}"
        if parsed.port:
            netloc += f":{port}"

        # 生成完整DSN
        full_dsn = urlunparse((
            parsed.scheme,
            netloc,
            parsed.path,
            parsed.params,
            parsed.query,
            parsed.fragment
        ))

        # 生成连接参数字典（类型安全）
        connection_params = {
            'host': hostname,
            'port': port,
            'user': unquote(username),
            'password': unquote(db_password),  # 注意这里返回明文用于连接
            'database': database,
            'charset': 'utf8mb4',
            'connect_timeout': 5
        }

        return full_dsn, connection_params

    def _get_metadata(self, conn_params: dict[str, Any]) -> dict[str, Any]:
        """
        获取数据库元数据（表结构信息）
        
        返回结构示例：
        {
            "tables": [
                {
                    "name": "users",
                    "columns": [
                        {"name": "id", "type": "int", "comment": "主键ID"},
                        {"name": "name", "type": "varchar(255)", "comment": "用户名"}
                    ]
                }
            ]
        }
        """
        metadata = {"tables": []}
        
        try:
            with pymysql.connect(
                host=conn_params['host'],
                port=conn_params['port'],
                user=conn_params['user'],
                password=conn_params['password'],
                database=conn_params['database'],
                charset=conn_params['charset'],
                cursorclass=DictCursor
            ) as conn:
                with conn.cursor() as cursor:
                    # 获取所有表信息
                    cursor.execute("""
                        SELECT TABLE_NAME AS table_name,
                               TABLE_COMMENT AS table_comment
                        FROM INFORMATION_SCHEMA.TABLES
                        WHERE TABLE_SCHEMA = DATABASE()
                        AND TABLE_TYPE = 'BASE TABLE'
                    """)
                    tables = cursor.fetchall()

                    # 获取每个表的列信息
                    for table in tables:
                        cursor.execute("""
                            SELECT COLUMN_NAME AS name,
                                   COLUMN_TYPE AS type,
                                   COLUMN_COMMENT AS comment,
                                   IS_NULLABLE AS nullable,
                                   COLUMN_KEY AS key_type
                            FROM INFORMATION_SCHEMA.COLUMNS
                            WHERE TABLE_SCHEMA = DATABASE()
                            AND TABLE_NAME = %s
                            ORDER BY ORDINAL_POSITION
                        """, (table['table_name'],))
                        
                        columns = []
                        for col in cursor.fetchall():
                            columns.append({
                                "name": col['name'],
                                "type": col['type'],
                                "comment": col['comment'] or "",
                                "nullable": col['nullable'] == 'YES',
                                "primary_key": col['key_type'] == 'PRI'
                            })
                        
                        metadata['tables'].append({
                            "name": table['table_name'],
                            "comment": table['table_comment'] or "",
                            "columns": columns
                        })
        
        except pymysql.Error as e:
            code, msg = e.args
            error_map = {
                1142: ("权限不足，无法访问元数据表", 403),
                1045: ("数据库认证失败", 401),
                2003: ("无法连接数据库服务器", 503)
            }
            error_info = error_map.get(code, (f"数据库错误: {msg}", 500))
            raise RuntimeError(f"{error_info[0]} (错误码: {code})") from e
            
        return metadata
    
    def _execute_sql_generator(self,sql: str, conn_params: dict[str, Any]) -> Generator[dict[str, Any], None, None]:
        """
        基于PyMySQL的SQL执行生成器函数
        :param sql: 待执行的SQL语句
        :param conn_params: 数据库连接参数字典
        :yield: 返回包含执行状态和数据的字典
        """
        import re
        pattern = r'^```sql(.*?)$$$'
        cleaned_sql = re.sub(pattern, r'\1', sql, flags=re.DOTALL)
        print(f"cleaned_sql:{cleaned_sql}")
        connection = None
        try:
            # 建立数据库连接（引用[2,5](@ref)）
            connection = pymysql.connect(
                host=conn_params['host'],
                user=conn_params['user'],
                password=conn_params['password'],
                database=conn_params['database'],
                charset='utf8mb4',  # 必须指定字符集（引用[2,5](@ref)）
                cursorclass=DictCursor  # 返回字典类型结果（引用[2,5](@ref)）
            )
            with connection.cursor() as cursor:
                # 执行SQL语句
                cursor.execute(cleaned_sql)
                
                # 获取列名和数据（引用[2,5](@ref)）
                columns = [desc[0] for desc in cursor.description]
                results = cursor.fetchall()
                
                # 生成结果集
                yield {
                    "status": "success",
                    "sql": cleaned_sql,
                    "columns": columns,
                    "data": results
                }
                
        except pymysql.MySQLError as e:
            # 捕获数据库特定错误（引用[5](@ref)）
            yield {
                "status": "error",
                "message": f"Database error: {str(e)}"
            }
        except Exception as ex:
            # 捕获其他异常（引用[5](@ref)）
            yield {
                "status": "error",
                "message": f"Execution error: {str(ex)}"
            }
        finally:
            # 资源释放（引用[2,5](@ref)）
            if connection and connection.open:
                connection.close()
                yield {
                    "status": "info",
                    "message": "数据库连接已关闭"
                }
