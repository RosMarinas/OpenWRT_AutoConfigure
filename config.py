from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests
import paramiko
from pathlib import Path
from uci_splitter_Add_Coment import UCISplitter  # 导入分块处理器
from openai import OpenAI
from FlagEmbedding import FlagAutoModel  # 确保 FlagEmbedder 已正确导入
import faiss
import numpy as np
import hashlib
import os
import re
import json  # 新增：用于保存和加载映射表
os.environ["TOKENIZERS_PARALLELISM"] = "false"

app = FastAPI()

# 初始化
UCI_CONFIG_DIR = Path("uci_configs")
VECTOR_DB_PATH = Path("vector_db.index")
UCI_AnnotatION_DIR = Path("uci_annotations")
Embedding_DIR = Path("bge_m3")  # 替换为你的模型目录
MAPPINGS_PATH = Path("vector_mappings.json")  # 新增：映射表保存路径
splitter = UCISplitter(max_chunk_size=500, overlap=20)
model = FlagAutoModel.from_finetuned('BAAI/bge-m3', use_fp16=True)
vector_db = None  # 延迟初始化
# 全局变量：记录每个配置块文件对应的向量ID（路径→ID）
file_path_to_vector_id = {}
vector_id_to_file_path = {} 

class UserRequest(BaseModel):
    command: str
    router_ip: str

class MessageCollector:
    def __init__(self):
        self.messages = []
    
    def collect(self, message):
        self.messages.append(message)
        print(message, flush=True)  # Still print to console
    
    def get_messages(self):
        messages = self.messages.copy()  # Return a copy to avoid race conditions
        return messages
    
    def clear(self):
        self.messages = []

# Create a global message collector
message_collector = MessageCollector()

def collect_print(message):
    """Collect a message and print it to console"""
    message_collector.collect(message)

def get_embedding(text):
    embedding = model.encode(text, batch_size=1, max_length=512)['dense_vecs']
    if embedding is None or len(embedding) == 0:
        raise ValueError("嵌入向量为空或未生成")
    return embedding

# 新增：保存映射表到文件
def save_mappings():
    """将文件路径与向量ID的映射表保存到文件"""
    with open(MAPPINGS_PATH, "w") as f:
        json.dump({
            "file_to_id": {k: int(v) for k, v in file_path_to_vector_id.items()},
            "id_to_file": {str(k): v for k, v in vector_id_to_file_path.items()}
        }, f)
    collect_print(f"映射表已保存到 {MAPPINGS_PATH}，包含 {len(file_path_to_vector_id)} 项")

# 新增：从文件加载映射表
def load_mappings():
    """从文件加载文件路径与向量ID的映射表"""
    global file_path_to_vector_id, vector_id_to_file_path
    if not MAPPINGS_PATH.exists():
        collect_print(f"映射表文件 {MAPPINGS_PATH} 不存在，使用空映射表")
        return
    
    try:
        with open(MAPPINGS_PATH, "r") as f:
            data = json.load(f)
            file_path_to_vector_id = {k: int(v) for k, v in data["file_to_id"].items()}
            vector_id_to_file_path = {int(k): v for k, v in data["id_to_file"].items()}
        collect_print(f"映射表已加载，包含 {len(file_path_to_vector_id)} 项")
    except Exception as e:
        collect_print(f"加载映射表出错：{e}，使用空映射表")
        file_path_to_vector_id = {}
        vector_id_to_file_path = {}

def initialize_vector_db(dim=1024):
    """初始化支持混合知识类型的向量数据库"""
    global vector_db
    if VECTOR_DB_PATH.exists():
        vector_db = faiss.read_index(str(VECTOR_DB_PATH))
        # 新增：加载映射表
        load_mappings()
        collect_print(f"向量数据库已加载，包含 {vector_db.ntotal} 个向量")
    else:
        # 使用分层索引提高检索效率（配置块+知识单元混合存储）
        quantizer = faiss.IndexFlatL2(dim)
        vector_db = faiss.IndexIVFFlat(quantizer, dim, 100)  # nlist=100
        vector_db.train(np.random.rand(1000, dim).astype('float32'))
    vector_db.is_trained = True

def get_modified_packages(script):
    """从生成的脚本中提取修改的配置模块（package）"""
    package_pattern = re.compile(r'uci (add|set|delete|get) (\S+)\.')  # 匹配 uci 命令中的 package
    packages = set()
    for line in script.split('\n'):
        match = package_pattern.search(line)
        if match: 
            package = match.group(2).split('.')[0]  # 提取 package 名称
            packages.add(package)
            collect_print(f"提取到配置模块：{package}")
        # else:
        #     collect_print(f"未提取到配置模块：{line}")
    
    if not packages:
        raise ValueError("未提取到任何配置模块")
    collect_print(f"提取到修改的配置模块：{packages}")
    return packages


def sync_modified_config(router_ip, packages):
    """仅同步指定模块的配置并更新向量数据库（修正版）"""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(router_ip, username='root', key_filename='/home/soyorin/.ssh/id_rsa.pub')
    collect_print(f"连接到路由器：{router_ip}")
    
    # 1. 收集所有待处理的旧文件路径（配置块 + 注释文件）
    old_config_files = []
    old_annotation_files = []
    for package in packages:
        pkg_name = package.replace('.', '_')
        # 旧配置块文件（如 package_part1.txt）
        old_config_files.extend(UCI_CONFIG_DIR.glob(f"{pkg_name}_part*.txt"))
        # 旧注释文件（与配置块文件同名，位于注释目录）
        old_annotation_files.extend(UCI_AnnotatION_DIR.glob(f"{pkg_name}_part*.txt"))
    
    # 2. 从向量数据库删除旧向量（通过配置块文件路径获取向量ID）
    delete_vector_ids = []
    for file in old_config_files:
        vector_id = file_path_to_vector_id.get(str(file), -1)
        if vector_id != -1:
            delete_vector_ids.append(vector_id)
    if delete_vector_ids:
        vector_db.remove_ids(np.array(delete_vector_ids, dtype=np.int64))
        # 更新映射表
        for file in old_config_files:
            file_path = str(file)
            if file_path in file_path_to_vector_id:
                del file_path_to_vector_id[file_path]
        for vector_id in delete_vector_ids:
            if vector_id in vector_id_to_file_path:
                del vector_id_to_file_path[vector_id]
        collect_print(f"已删除 {len(delete_vector_ids)} 个旧向量")
    
    # 3. 删除文件系统中的旧文件（先删旧，再生成新）
    for file in old_config_files:
        file.unlink()
        collect_print(f"删除旧配置块文件：{file.name}")
    for file in old_annotation_files:
        file.unlink()
        collect_print(f"删除旧注释文件：{file.name}")
    
    # 4. 生成新配置块和注释文件
    updated_chunks = []
    for package in packages:
        pkg_name = package.replace('.', '_')
        # 获取新配置
        cmd = f"uci export {package}"
        _, stdout, _ = ssh.exec_command(cmd)
        package_config = stdout.read().decode()
        if not package_config:
            continue
        
        # 临时文件存储新配置
        temp_file = UCI_CONFIG_DIR / f"temp_{package}.txt"
        collect_print(f"生成临时文件：{temp_file}")
        with open(temp_file, 'w') as f:
            f.write(package_config)
        
        # 分块处理新配置（生成新的配置块和注释文件）
        splitter.split_config(temp_file, UCI_CONFIG_DIR, UCI_AnnotatION_DIR)
        temp_file.unlink()  # 删除临时文件

        
        # 收集新生成的配置块文件
        new_config_chunks = list(UCI_CONFIG_DIR.glob(f"{pkg_name}_part*.txt"))
        collect_print(f"新生成的配置块文件：{new_config_chunks}")
        updated_chunks.extend(new_config_chunks)
    
    ssh.close()
    
    # 5. 添加新向量并更新映射表
    for chunk_file in updated_chunks:
        file_path = str(chunk_file)
        content = chunk_file.read_text()
        embedding = get_embedding(content).reshape(1, -1)
        vector_id = vector_db.ntotal
        vector_db.add(embedding)
        # 更新双向映射
        file_path_to_vector_id[file_path] = vector_id
        vector_id_to_file_path[vector_id] = file_path
    
    faiss.write_index(vector_db, str(VECTOR_DB_PATH))
    # 新增：保存映射表
    save_mappings()
    collect_print(f"配置已更新，新增 {len(updated_chunks)} 个配置块")
    
    return updated_chunks

def sync_and_split_config(router_ip):
    """同步并分块处理 UCI 配置"""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(router_ip, username='root', key_filename='/home/soyorin/.ssh/id_rsa.pub')
    # 清空旧映射（首次初始化或全量更新时）
    file_path_to_vector_id.clear()
    vector_id_to_file_path.clear()
    # 获取完整配置
    _, stdout, _ = ssh.exec_command("uci export")
    full_config = stdout.read().decode()
    ssh.close()
    collect_print(f"完整配置长度: {len(full_config)}")
    collect_print("aleady get full config")
    # 分块处理
    UCI_CONFIG_DIR.mkdir(exist_ok=True)
    input_file = UCI_CONFIG_DIR / "full_config.uci"
    with open(input_file, 'w') as f:
        f.write(full_config)
    
    splitter.split_config(input_file, UCI_CONFIG_DIR, UCI_AnnotatION_DIR)
    
    # 更新向量数据库
    for chunk_file in UCI_CONFIG_DIR.glob("*.txt"):
        if chunk_file.name.startswith("knowledge_"):  # 跳过知识单元文件
            continue
        with open(chunk_file) as f:
            content = f.read()
        embedding = get_embedding(content).reshape(1, -1)  # 将嵌入向量调整为 (1, dim) 的形状
        vector_id = vector_db.ntotal  # 当前向量总数即新向量ID
        vector_db.add(embedding)
        file_path = str(chunk_file)
        file_path_to_vector_id[file_path] = vector_id
        vector_id_to_file_path[vector_id] = file_path
    faiss.write_index(vector_db, str(VECTOR_DB_PATH))
    # 新增：保存映射表
    save_mappings()
    collect_print(f"向量数据库已初始化，存储在 {VECTOR_DB_PATH} 中")

def retrieve_relevant_chunks(query, top_k=2):
    """检索最相关的配置块"""
    collect_print(f"检索相关配置块：{query}")
    query_embedding = get_embedding(query).reshape(1, -1)
    distances, indices = vector_db.search(query_embedding, top_k)
    
    relevant_chunks = []
    for i, vector_id in enumerate(indices[0]):  # 遍历向量ID
        # 通过双向映射获取文件路径（处理可能的无效ID，如旧ID已删除）
        file_path = vector_id_to_file_path.get(int(vector_id), None)
        collect_print(f"vector_id: {vector_id}, file_path: {file_path}")
        if not file_path:
            collect_print(f"警告：向量ID {vector_id} 无对应文件路径")
            continue
        try:
            with open(file_path) as f:
                content = f.read()
                relevant_chunks.append(content)
            # 打印调试信息（可选）
            collect_print(f"距离: {distances[0][i]}，配置块路径: {file_path}")
        except FileNotFoundError:
            collect_print(f"警告：文件 {file_path} 不存在")

    collect_print(f"检索到 {len(relevant_chunks)} 个相关配置块")
    return "\n\n".join(relevant_chunks)
    
def extract_code_block(text):
    """提取代码块（首尾用 ``` 包裹）"""
    lines = text.split("\n")
    code_block = []
    in_code_block = False
    for line in lines:
        if line.startswith((" ```bash","```bash")) and not in_code_block:
            in_code_block = True
            continue
        if in_code_block:
            if line.startswith("```"):
                break
            code_block.append(line)
    return "\n".join(code_block) if code_block else None

def validate_uci_script(script: str) -> tuple[bool, str]:
    """
    Validate a UCI configuration script.
    Returns (is_valid, error_message)
    """
    # Basic syntax checks
    if not script.strip():
        return False, "Empty script"
    
    collect_print("脚本不为空")
    # Check for dangerous commands
    dangerous_commands = ['rm ', 'mv ', ' dd ', 'format ', 'mkfs ', '>', '>>']
    
    for line in script.split('\n'):
        if line.startswith('#'):
            continue
        if "#" in line:
            line = line.split("#")[0]
        if "\"" in line:
            line = line.split("\"")[0]
            #collect_print(f"line `{line}`")
        for command in dangerous_commands:
            if command in line:
                collect_print(f"在line `{line}`   存在危险命令 {command} ")
                return False, f"危险命令 {command} 存在，检查不通过"
    collect_print("危险命令检查通过")
    
    # Add shebang and error handling if not present
    if not script.startswith('#!/bin/sh'):
        script = '#!/bin/sh\n' + script
    
    if 'trap' not in script:
        script = script + '\ntrap \'echo "An error occurred in the script. Exiting..."; exit 1\' ERR'
    
    collect_print("脚本验证通过")
    return True, script

def execute_script_on_router(script: str, router_ip: str) -> tuple[bool, str]:
    """
    Execute the script on the router via SSH and return the result.
    Returns (success, message)
    """
    try:
        collect_print("开始执行脚本")
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(router_ip, username='root', key_filename='/home/soyorin/.ssh/id_rsa.pub')
        collect_print(f"连接到路由器：{router_ip}")

        # 创建目录（确保存在）
        ssh.exec_command('mkdir -p /tmp/uci_agent')
        
        # 使用 SFTP 上传文件（更可靠）
        sftp = ssh.open_sftp()
        with sftp.open('/tmp/uci_agent/uci_script.sh', 'w') as f:
            f.write(script)
        sftp.close()
        collect_print("脚本已上传到路由器")

        # 设置执行权限
        ssh.exec_command('chmod +x /tmp/uci_agent/uci_script.sh')
        collect_print("脚本已设置为可执行")

        # 执行脚本
        stdin, stdout, stderr = ssh.exec_command(
            '/tmp/uci_agent/uci_script.sh',
            bufsize=1,
            timeout=30
        )
        stdin.close()
        
        collect_print("脚本正在执行")
        output = stdout.read().decode()
        error = stderr.read().decode()
        collect_print("脚本执行完成")
        collect_print(f"输出：{output}")
        collect_print(f"错误：{error}")

        # 清理文件
        ssh.exec_command('rm /tmp/uci_agent/uci_script.sh')
        ssh.close()

        if error:
            return False, f"Script execution failed: {error}"
        return True, output
        
    except Exception as e:
        return False, f"SSH connection failed: {str(e)}"

@app.post("/generate_script")
async def generate_script(request: UserRequest):
    global vector_db
    
    # Clear previous messages
    message_collector.clear()
    
    try:
        # 服务启动时已完成首次全量同步，后续仅处理增量更新
        if not list(UCI_CONFIG_DIR.glob("*.txt")):
            initial_router_ip = "192.168.6.1"  # 需配置默认 IP 或从配置文件读取
            sync_and_split_config(initial_router_ip)
        
        # 检索相关配置块（包含历史积累的知识块）
        relevant_configs = retrieve_relevant_chunks(request.command)
        #collect_print(f"相关配置块：\n{relevant_configs}")
        
        # 生成脚本（原有逻辑不变）
        prompt = f"""
        用户指令：{request.command}
        相关配置上下文：
        {relevant_configs[:2000]}  # 控制上下文长度
        
        请生成符合以下要求的uci配置脚本：
        1. 仅使用 uci 命令和 /etc/init.d/ 服务管理
        2. 包含配置验证步骤（如 uci get）
        3. 仅生成一个bash脚本
        """
        #collect_print(f"提示词：\n{prompt}")
        client = OpenAI(
            base_url="http://localhost:11434/v1",
            api_key="sk-no-key-required"
        )
        response = client.chat.completions.create(
            model="qwen2.5-coder:7b",
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=0.6,
            max_tokens=4096
        )
        collect_print(f"LLM 响应：....")
        
        script = extract_code_block(response.choices[0].message.content)
        collect_print(f"提取的脚本：....")
        if not script:
            raise HTTPException(status_code=100, detail="脚本生成失败")
        
        # Validate the script
        is_valid, result = validate_uci_script(script)
        if not is_valid:
            raise HTTPException(status_code=114514, detail=result)
        
        # Execute the script on the router
        success, message = execute_script_on_router(result, request.router_ip)
        if not success:
            raise HTTPException(status_code=114515, detail=message)
        
        # 新增：知识积累与增量配置更新
        try:
            # 1. 解析脚本确定修改的配置模块
            modified_packages = get_modified_packages(script)
            
            if modified_packages:
                # 2. 同步更新后的模块配置
                collect_print(f"修改的配置模块：{modified_packages}")
                updated_chunks = sync_modified_config(request.router_ip, modified_packages)
                
                # 3. 创建知识单元文件（提问+脚本+相关配置）
                knowledge_dir = UCI_CONFIG_DIR / "knowledge"
                knowledge_dir.mkdir(exist_ok=True)
                collect_print(f"知识单元目录：{knowledge_dir}")
                knowledge_id = hashlib.md5(request.command.encode()).hexdigest()[:8]
                knowledge_file = knowledge_dir / f"knowledge_{knowledge_id}.txt"
                with open(knowledge_file, 'w') as f:
                    f.write(f"#User answer：{request.command}\n\n# scripts:\n{script}\n\n# relevant configs:\n{relevant_configs}")
                
                # 4. 将知识单元嵌入向量加入数据库
                knowledge_embedding = get_embedding(knowledge_file.read_text()).reshape(1, -1)
                vector_id = vector_db.ntotal
                vector_db.add(knowledge_embedding)
                file_path = str(knowledge_file)
                file_path_to_vector_id[file_path] = vector_id
                vector_id_to_file_path[vector_id] = file_path
                faiss.write_index(vector_db, str(VECTOR_DB_PATH))
                # 新增：保存映射表
                save_mappings()
                
                collect_print(f"新增知识单元：{knowledge_file}")
            
            # 5. 存储到注释目录（可用于后续检索增强）
            annotation_file = UCI_AnnotatION_DIR / f"knowledge_{knowledge_id}.txt"
            with open(annotation_file, 'w') as f:
                f.write(f"answer:{request.command} and the script used to solve it.")
        
        except Exception as e:
            collect_print(f"增量更新失败：{e}")
        
        # Get all collected messages
        messages = message_collector.get_messages()
        
        return {
            "script": script,
            "router_ip": request.router_ip,
            "execution_result": message,
            "messages": messages
        }
    
    except Exception as e:
        collect_print(f"Error: {str(e)}")
        raise

# 启动时初始化向量数据库
initialize_vector_db()