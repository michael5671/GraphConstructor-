import os
import networkx as nx
import yaml
import re
from tree_sitter_languages import get_language, get_parser

class PolyglotGraphBuilder:
    def __init__(self, repo_path):
        self.repo_path = repo_path
        self.graph = nx.DiGraph()
        
        # --- CẤU HÌNH PARSER CHO TỪNG NGÔN NGỮ ---
        # --- CẤU HÌNH PARSER ĐÃ SỬA LỖI ---
        self.parsers = {
            '.py': {
                'lang': get_language('python'),
                'parser': get_parser('python'),
                'queries': {
                    'defs': """
                        (class_definition name: (identifier) @name) @def.class
                        (function_definition name: (identifier) @name) @def.func
                    """,
                    'calls': """
                        (call function: (identifier) @call) @ref
                        (call function: (attribute attribute: (identifier) @call)) @ref
                    """
                }
            },
            '.yaml': {
                'lang': get_language('yaml'),
                'parser': get_parser('yaml'),
                'queries': {
                    'defs': """
                        (block_mapping_pair key: (flow_node) @name) @def.key
                    """,
                    'calls': "" 
                }
            },
            '.yml': { 'alias': '.yaml' },
            
            # [FIXED] Dockerfile: Bỏ field "image:", chỉ match node con (image_spec)
            'Dockerfile': {
                'lang': get_language('dockerfile'),
                'parser': get_parser('dockerfile'),
                'queries': {
                    'defs': """
                        (from_instruction (image_spec) @name) @def.image
                    """,
                    'calls': ""
                }
            },
            
            # [FIXED] Terraform/HCL: Bỏ field "type:" và "labels:", dựa vào thứ tự node con
            # Cấu trúc thường là: block -> identifier (resource type) -> string_lit (name)
            '.tf': { 
                'lang': get_language('hcl'),
                'parser': get_parser('hcl'),
                'queries': {
                    'defs': """
                        (block 
                            (identifier) @type 
                            (string_lit) @name
                        ) @def.resource
                    """,
                    'calls': ""
                }
            }
        }
        
        # Xử lý alias (ví dụ .yml -> .yaml)
        keys_to_add = {}
        for ext, config in self.parsers.items():
            if 'alias' in config:
                keys_to_add[ext] = self.parsers[config['alias']]
        self.parsers.update(keys_to_add)

    def get_node_id(self, file_path, name, kind=""):
        rel_path = os.path.relpath(file_path, self.repo_path)
        # Clean ID để tránh lỗi Mermaid/YAML
        clean_name = name.replace('"', '').replace("'", "")
        return f"{rel_path}::{clean_name}"
    def is_valid_identifier(self, name):
        if not name: return False
        # Chỉ chấp nhận chữ, số, _, -, . và :
        if re.search(r'[^\w\-\.\:]', name): 
            return False
        return True

    # --- [THÊM MỚI] Hàm leo cây để lấy path YAML (a.b.c) ---
    def get_yaml_full_path(self, node, code_bytes):
        path = []
        current_text = code_bytes[node.start_byte:node.end_byte].decode('utf8')
        path.append(current_text)
        
        # Leo ngược lên cha
        curr = node.parent
        while curr:
            if curr.type == 'block_mapping_pair':
                key_node = curr.child_by_field_name('key')
                if key_node and key_node != node:
                    key_text = code_bytes[key_node.start_byte:key_node.end_byte].decode('utf8')
                    path.insert(0, key_text)
            curr = curr.parent
        return ".".join(path)

    def parse_file(self, file_path):
        filename = os.path.basename(file_path)
        _, ext = os.path.splitext(filename)
        
        # Xử lý đặc biệt cho Dockerfile
        if filename == 'Dockerfile': config = self.parsers.get('Dockerfile')
        else: config = self.parsers.get(ext)

        if not config: return 

        rel_path = os.path.relpath(file_path, self.repo_path)
        self.graph.add_node(rel_path, type="file", lang=ext or 'docker')

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                code_str = f.read()
            
            # [QUAN TRỌNG] Chuyển sang bytes để xử lý tree-sitter chính xác vị trí
            code_bytes = bytes(code_str, "utf8")
            tree = config['parser'].parse(code_bytes)
            
            query = config['lang'].query(config['queries']['defs'])
            captures = query.captures(tree.root_node)
            
            # Xử lý Captures (Hỗ trợ cả version cũ trả về list và mới trả về dict)
            # Nếu captures là dict (version mới), ta convert sang list tuples để loop chung logic
            if isinstance(captures, dict):
                capture_list = []
                for name, nodes in captures.items():
                    for node in nodes:
                        capture_list.append((node, name))
            else:
                capture_list = captures

            for node, capture_name in capture_list:
                if capture_name == 'name': 
                    name = ""
                    # 1. Logic YAML Phân cấp (database -> database.db_host)
                    if ext in ['.yaml', '.yml']:
                        name = self.get_yaml_full_path(node, code_bytes)
                    else:
                        name = code_bytes[node.start_byte:node.end_byte].decode('utf8')

                    # 2. Làm sạch tên
                    name = name.replace('"', '').replace("'", "").strip()
                    
                    # 3. Lọc rác (Loại bỏ 'nt(' hoặc tên biến dị dạng)
                    if not self.is_valid_identifier(name):
                        continue

                    # 4. Tạo Node
                    node_id = self.get_node_id(file_path, name)
                    self.graph.add_node(node_id, type="definition", name=name)
                    self.graph.add_edge(rel_path, node_id, relation="contains")
                    
        except Exception as e:
            print(f"Lỗi parse file {rel_path}: {e}")
                    
        except Exception as e:
            print(f"Lỗi parse file {rel_path}: {e}")

    def build_cross_reference(self):
        print("Đang phân giải liên kết toàn bộ repo...")
        definitions = {} 
        
        # 1. Indexing: Gom tất cả định nghĩa lại
        for node, attr in self.graph.nodes(data=True):
            if attr.get('type') == 'definition':
                name = attr.get('name')
                if name:
                    # Lưu tên gốc (VD: database.db_host)
                    if name not in definitions: definitions[name] = []
                    definitions[name].append(node)
                    
                    # [QUAN TRỌNG] Tạo Alias cho các tên phân cấp
                    # Giúp 'db_host' trong main.py tìm thấy 'database.db_host' trong yaml
                    if "." in name:
                        leaf_name = name.split(".")[-1]
                        if leaf_name not in definitions: definitions[leaf_name] = []
                        definitions[leaf_name].append(node)

        # 2. Scanning: Quét nội dung file để tìm reference
        file_nodes = [n for n, a in self.graph.nodes(data=True) if a.get('type') == 'file']
        
        for file_node in file_nodes:
            full_path = os.path.join(self.repo_path, file_node)
            try:
                with open(full_path, "r", encoding="utf-8") as f:
                    content = f.read()
                
                for def_name, target_nodes in definitions.items():
                    # Rule: Tên phải dài > 3 ký tự và xuất hiện trong content
                    if len(def_name) > 3 and def_name in content:
                        for target_node in target_nodes:
                            # Không tự nối chính nó
                            if not target_node.startswith(file_node):
                                self.graph.add_edge(file_node, target_node, relation="references")
            except:
                pass

    def build(self):
        for root, _, files in os.walk(self.repo_path):
            # Bỏ qua git, venv
            if '.git' in root or 'venv' in root or '__pycache__' in root:
                continue
            for file in files:
                self.parse_file(os.path.join(root, file))
        
        self.build_cross_reference()
        print(f"Build xong! Nodes: {self.graph.number_of_nodes()}, Edges: {self.graph.number_of_edges()}")

    def export_mermaid(self):
        lines = ["flowchart TD"]
        
        def clean_id(text):
            return text.replace("/", "_").replace(".", "_").replace(":", "_").replace("-", "_").replace(" ", "_")

        # 1. Vẽ Subgraphs (Mỗi file là 1 cụm)
        files = [n for n, a in self.graph.nodes(data=True) if a.get('type') == 'file']
        
        for f in files:
            f_id = clean_id(f)
            lang = self.graph.nodes[f].get('lang', '')
            lines.append(f'    subgraph cluster_{f_id} ["{os.path.basename(f)} ({lang})"]')
            
            # Vẽ 1 node "Neo" đại diện cho chính file đó (để nối dây reference từ file này đi ra)
            lines.append(f'        {f_id}["📄 {os.path.basename(f)}"]')
            lines.append(f'        style {f_id} fill:#f9f,stroke:#333,stroke-width:2px')

            # Vẽ các Definitions bên trong
            children = [v for u, v, d in self.graph.out_edges(f, data=True) if d['relation'] == 'contains']
            for child in children:
                c_id = clean_id(child)
                c_name = self.graph.nodes[child]['name']
                # Icon tùy loại
                icon = "🔧" if lang == '.py' else "🐳" if lang == 'Dockerfile' else "⚙️"
                lines.append(f'        {c_id}("{icon} {c_name}")')
                # Nối node File -> node Definition (quan hệ chứa)
                lines.append(f'        {f_id} --- {c_id}')
            
            lines.append('    end')

        # 2. Vẽ Reference (Liên kết giữa các file)
        # Logic: File A (node neo) --> Definition B (node con của file khác)
        for u, v, d in self.graph.edges(data=True):
            if d['relation'] == 'references':
                u_id = clean_id(u) # ID của File nguồn
                v_id = clean_id(v) # ID của Def đích
                lines.append(f'    {u_id} -.-> {v_id}')

        return "\n".join(lines)
    
    def export_yaml_whole_repo(self, include_source=True): # Thêm tham số include_source
        """Xuất YAML kèm theo Source Code để LLM review được"""
        repo_data = []
        file_nodes = [n for n, a in self.graph.nodes(data=True) if a.get('type') == 'file']
        
        for f_node in file_nodes:
            file_path = os.path.join(self.repo_path, f_node)
            
            # Đọc nội dung file (nếu được yêu cầu)
            source_content = ""
            if include_source:
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        source_content = f.read()
                except:
                    source_content = "[Binary or Unreadable]"

            file_entry = {
                "path": f_node,
                # Nhúng code vào đây để LLM đọc
                "source_code": source_content, 
                "definitions": [],
                "references_to": []
            }
            
            # ... (Phần logic lấy definitions và references giữ nguyên) ...
            children = [v for u, v, d in self.graph.out_edges(f_node, data=True) if d['relation'] == 'contains']
            for child in children:
                file_entry["definitions"].append(self.graph.nodes[child]['name'])

            refs = [v for u, v, d in self.graph.out_edges(f_node, data=True) if d['relation'] == 'references']
            for ref in refs:
                ref_name = self.graph.nodes[ref].get('name', ref)
                file_entry["references_to"].append(ref_name)
            
            # Clean up empty lists
            if not file_entry["definitions"]: del file_entry["definitions"]
            if not file_entry["references_to"]: del file_entry["references_to"]
            
            repo_data.append(file_entry)
            
        return yaml.dump(repo_data, sort_keys=False, allow_unicode=True)
if __name__ == "__main__":
    # Thay đường dẫn tới repo của bạn
    REPO_PATH = "./simpler_repo/mini_polyglot_repo" 
    
    builder = PolyglotGraphBuilder(REPO_PATH)
    builder.build()
    
    # Lưu ra file để feed cho LLM
    with open("whole_repo_context.yaml", "w", encoding="utf-8") as f:
        f.write(builder.export_yaml_whole_repo())

    # 2. Xuất Mermaid để xem hình
    mermaid_code = builder.export_mermaid()
    with open("repo_graph.mmd", "w", encoding="utf-8") as f:
        f.write(mermaid_code)
    
    print("Đã xuất file: whole_repo_context.yaml")
    print("\nCopy nội dung file .mmd vào https://mermaid.live để xem biểu đồ!")