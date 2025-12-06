import os
import networkx as nx
import yaml
from tree_sitter_languages import get_language, get_parser

# --- CẤU HÌNH TREE-SITTER QUERIES (Section 4.2) ---
# Query để tìm định nghĩa Hàm và Lớp

DEF_QUERY_SCM = """
(class_definition
  name: (identifier) @class.name) @class.def

(function_definition
  name: (identifier) @function.name) @function.def
"""


# Query để tìm lời gọi hàm (Call sites)
# Lưu ý: Đây là dạng đơn giản, chưa xử lý nested calls phức tạp
CALL_QUERY_SCM = """
(call
  function: (identifier) @call.name) @call.site

(call
  function: (attribute
    attribute: (identifier) @call.method)) @call.site
"""

class RepoGraphBuilder:
    def __init__(self, repo_path):
        self.repo_path = repo_path
        self.graph = nx.DiGraph()
        self.language = get_language('python')
        self.parser = get_parser('python')
        
        # Từ điển toàn cục để map function_name -> [list of node_ids]
        # Phục vụ cho "Naive Name Resolution" (Section 5.2)
        self.global_definitions = {} 

    def get_node_id(self, file_path, name):
        """Tạo ID duy nhất: path/to/file.py::function_name"""
        rel_path = os.path.relpath(file_path, self.repo_path)
        return f"{rel_path}::{name}"

    def parse_file(self, file_path):
        """Đọc file, parse AST và tạo Nodes"""
        with open(file_path, "r", encoding="utf-8") as f:
            code = f.read()
        
        tree = self.parser.parse(bytes(code, "utf8"))
        root_node = tree.root_node
        rel_path = os.path.relpath(file_path, self.repo_path)

        # 1. Tạo Node cho File
        file_node_id = rel_path
        self.graph.add_node(file_node_id, type="file", path=rel_path)

        # 2. Tìm Definitions (Class/Function)
        query = self.language.query(DEF_QUERY_SCM)
        captures = query.captures(root_node)
        
        for node, capture_name in captures:
            if capture_name in ["class.name", "function.name"]:
                name = code[node.start_byte:node.end_byte]
                node_id = self.get_node_id(file_path, name)
                node_type = "class" if capture_name == "class.name" else "function"
                
                # Thêm Node vào Graph
                self.graph.add_node(node_id, type=node_type, name=name, file=rel_path)
                
                # Thêm cạnh "defines": File -> Function
                self.graph.add_edge(file_node_id, node_id, relation="defines")

                # Lưu vào global dict để resolution sau này
                if name not in self.global_definitions:
                    self.global_definitions[name] = []
                self.global_definitions[name].append(node_id)

    def resolve_dependencies(self):
        """Quét lại để tìm Calls và nối cạnh (Nâng cấp)"""
        file_nodes = [n for n, attr in self.graph.nodes(data=True) if attr.get('type') == 'file']
        
        for file_node_id in file_nodes:
            file_path = os.path.join(self.repo_path, file_node_id)
            if not os.path.exists(file_path): continue
            
            with open(file_path, "r", encoding="utf-8") as f:
                code = f.read()
            tree = self.parser.parse(bytes(code, "utf8"))
            
            query = self.language.query(CALL_QUERY_SCM)
            captures = query.captures(tree.root_node)
            
            # --- LOGIC MỚI ---
            found_calls = set()
            for node, capture_name in captures:
                # Nếu capture là @call.name -> lấy text trực tiếp
                # Nếu capture là @call.method -> lấy text của method (bỏ qua object phía trước)
                if capture_name in ["call.name", "call.method"]:
                    call_name = code[node.start_byte:node.end_byte]
                    found_calls.add(call_name)
            
            # Tạo cạnh từ các call tìm được
            for call_name in found_calls:
                if call_name in self.global_definitions:
                    possible_targets = self.global_definitions[call_name]
                    for target_id in possible_targets:
                        # Logic đơn giản: Nối File -> Function được gọi
                        if target_id != file_node_id: 
                            self.graph.add_edge(file_node_id, target_id, relation="calls")

    def build(self):
        """Quy trình Ingestion (Section 4.1)"""
        for root, _, files in os.walk(self.repo_path):
            for file in files:
                if file.endswith(".py"):
                    full_path = os.path.join(root, file)
                    self.parse_file(full_path)
        
        self.resolve_dependencies()
        print(f"Build xong! Nodes: {self.graph.number_of_nodes()}, Edges: {self.graph.number_of_edges()}")

    def export_mermaid(self):
        """Xuất ra Mermaid Chart (Section 6)"""
        lines = ["flowchart TD"]
        
        # Group by File (Subgraphs)
        files = [n for n, attr in self.graph.nodes(data=True) if attr['type'] == 'file']
        for f in files:
            lines.append(f'    subgraph "{f}"')
            # Tìm các hàm thuộc file này
            children = [v for u, v, d in self.graph.out_edges(f, data=True) if d['relation'] == 'defines']
            for child in children:
                name = self.graph.nodes[child]['name']
                # Sanitize ID cho mermaid (thay thế ký tự lạ)
                safe_id = child.replace("/", "_").replace(".", "_").replace(":", "_")
                lines.append(f'        {safe_id}["{name}"]')
            lines.append('    end')

        # Vẽ cạnh Calls
        for u, v, d in self.graph.edges(data=True):
            if d['relation'] == 'calls':
                # Tìm caller thực sự (ở đây đang đơn giản hóa là File gọi Function)
                u_safe = u.replace("/", "_").replace(".", "_").replace(":", "_")
                v_safe = v.replace("/", "_").replace(".", "_").replace(":", "_")
                lines.append(f'    {u_safe} -.-> {v_safe}')
        
        return "\n".join(lines)

    def export_yaml(self):
        """Xuất ra YAML context cho LLM (Section 7.3)"""
        data = []
        files = [n for n, attr in self.graph.nodes(data=True) if attr['type'] == 'file']
        
        for f in files:
            file_obj = {"file": f, "functions": []}
            children = [v for u, v, d in self.graph.out_edges(f, data=True) if d['relation'] == 'defines']
            
            for child in children:
                func_node = self.graph.nodes[child]
                func_obj = {
                    "name": func_node['name'],
                    "calls": []
                }
                # Tìm xem hàm này/file này gọi đi đâu (Logic đơn giản hóa)
                # Lưu ý: Trong code build hiện tại, edge 'calls' đi từ File -> Target Function
                # Để chính xác hơn cần scope stack, nhưng tạm thời lấy từ File
                outgoing = [v for u, v, d in self.graph.out_edges(f, data=True) if d['relation'] == 'calls']
                func_obj["calls"] = list(set(outgoing)) # Unique
                
                file_obj["functions"].append(func_obj)
            data.append(file_obj)
            
        return yaml.dump(data, sort_keys=False, allow_unicode=True)

# --- CHẠY THỬ ---
if __name__ == "__main__":
    # Thay đường dẫn này bằng repo bạn muốn scan
    REPO_PATH = "./my_simple_repo" 
    
    # Tạo dummy data để test nếu chưa có repo
    if not os.path.exists(REPO_PATH):
        os.makedirs(REPO_PATH, exist_ok=True)
        with open(f"{REPO_PATH}/math_lib.py", "w") as f:
            f.write("def add(a, b): return a + b\nclass Calculator:\n    def sub(self, a, b): return a - b")
        with open(f"{REPO_PATH}/main.py", "w") as f:
            f.write("import math_lib\ndef main():\n    res = add(1, 2)\n    calc = Calculator()\n    res2 = calc.sub(3, 1)")

    builder = RepoGraphBuilder(REPO_PATH)
    builder.build()

    print("\n--- MERMAID OUTPUT (Copy vào Mermaid Live Editor) ---")
    print(builder.export_mermaid())

    print("\n--- YAML OUTPUT (Feed cho LLM) ---")
    print(builder.export_yaml())