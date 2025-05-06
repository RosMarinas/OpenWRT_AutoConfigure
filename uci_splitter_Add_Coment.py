from pathlib import Path
import re
import hashlib
from openai import OpenAI
import concurrent.futures

# 配置 OpenAI 客户端
client = OpenAI(
    base_url="http://localhost:11434/v1",
    api_key="sk-no-key-required"
)

class UCISplitter:
    def __init__(self, max_chunk_size=2000, overlap=20):
        """
        :param max_chunk_size: 单块最大字符数（建议为模型最大输入的 70-80%）
        :param overlap: 块间重叠字符数（保证上下文连贯）
        """
        self.max_chunk_size = max_chunk_size
        self.overlap = overlap
        self.package_pattern = re.compile(r'^package\s+(\S+)')
        self.config_header_pattern = re.compile(r'^config\s+(\S+)\s+\'(\S+)\'')
        self.last_buffer_hash = None  # 新增：记录上一次缓冲区的哈希值
        self.annotation_dict = {}  # 用于存储文件路径和注释的映射

    def generate_annotation(self, buffer_content, current_package, chunk_num):
        """使用 OpenAI API 生成注释"""
        prompt = f"Please generate a summary annotation for the {chunk_num}th chunk file of the {current_package} package,it would be great if used in simple and easy-to-understand statements. Don't copy the code to the summary. The file content is as follows: {buffer_content}"
        try:
            response = client.chat.completions.create(
                model="qwen2.5-coder:7b",
                messages=[
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=8192
            )
            print(f"Generated annotation:\n{response.choices[0].message.content}")
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"Error generating annotation: {e}")
            return f"This is the {chunk_num}th chunk file of the {current_package} package."

    def _flush_buffer(self, buffer, current_package, output_dir):
        """将缓冲区内容写入分块文件"""
        if not buffer:
            return None

        # 计算当前缓冲区的哈希值
        buffer_content = ''.join(buffer)
        current_hash = hashlib.md5(buffer_content.encode()).hexdigest()

        # 若内容与上一次相同，则跳过写入
        if current_hash == self.last_buffer_hash:
            return None

        # 写入文件并更新哈希缓存
        self.last_buffer_hash = current_hash
        safe_pkg = current_package.replace('.', '_')
        chunk_num = len(list(output_dir.glob(f"{safe_pkg}_*.txt"))) + 1
        filename = output_dir / f"{safe_pkg}_part{chunk_num}.txt"

        with open(filename, 'w') as f:
            f.write(f"# Parent Package: {current_package}\n\n{buffer_content}")

        return (filename, buffer_content, current_package, chunk_num)

    def split_config(self, input_file, output_dir, annotation_dir):
        output_dir = Path(output_dir)
        output_dir.mkdir(exist_ok=True)
        annotation_dir = Path(annotation_dir)
        annotation_dir.mkdir(exist_ok=True)

        current_package = None
        buffer = []
        buffer_size = 0
        tasks = []

        with open(input_file, 'r') as f:
            for line in f:
                # 检测 package 声明
                pkg_match = self.package_pattern.match(line)
                if pkg_match:
                    # 遇到新 package，先刷写旧缓冲区
                    if buffer:
                        task = self._flush_buffer(buffer, current_package, output_dir)
                        if task:
                            tasks.append(task)
                        buffer = []
                        buffer_size = 0
                    current_package = pkg_match.group(1)

                # 统计字符数（按实际计入模型的 token 计算方式简化处理）
                line_length = len(line)

                # 缓冲区即将超限时分割
                if buffer_size + line_length > self.max_chunk_size:
                    # 优先在 config 边界处分割
                    last_config_pos = -1
                    for i, bline in enumerate(reversed(buffer)):
                        if self.config_header_pattern.match(bline):
                            last_config_pos = len(buffer) - i - 1
                            break

                    if last_config_pos != -1:
                        # 分割为当前块和剩余部分
                        chunk_part1 = buffer[:last_config_pos + 1]
                        chunk_part2 = buffer[last_config_pos + 1:]

                        # 写入第一部分
                        task = self._flush_buffer(chunk_part1, current_package, output_dir)
                        if task:
                            tasks.append(task)

                        # 保留重叠部分
                        buffer = chunk_part1[-self.overlap:] + chunk_part2
                        buffer_size = sum(len(l) for l in buffer)
                    else:
                        # 无 config 边界，强制分割
                        task = self._flush_buffer(buffer, current_package, output_dir)
                        if task:
                            tasks.append(task)
                        buffer = []
                        buffer_size = 0

                buffer.append(line)
                buffer_size += line_length

            # 处理最后剩余内容
            if buffer:
                task = self._flush_buffer(buffer, current_package, output_dir)
                if task:
                    tasks.append(task)

        # 使用线程池并发生成注释
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future_to_task = {executor.submit(self.generate_annotation, buffer_content, current_package, chunk_num): (filename, buffer_content, current_package, chunk_num) for filename, buffer_content, current_package, chunk_num in tasks}
            for future in concurrent.futures.as_completed(future_to_task):
                filename, buffer_content, current_package, chunk_num = future_to_task[future]
                try:
                    annotation = future.result()
                    self.annotation_dict[str(filename)] = annotation
                    # 将注释写入单独的注释文件
                    annotation_filename = annotation_dir / filename.name
                    with open(annotation_filename, 'w') as f:
                        f.write(annotation)
                    print(f"Chunk file generated: {filename}")
                    print(f"Annotation file generated: {annotation_filename}")
                except Exception as exc:
                    print(f'Error generating annotation for {filename}: {exc}')

    