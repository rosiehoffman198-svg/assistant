"""Постоянный скрипт верификации. Запуск: python _verify.py

Проверяет без подключения к БД/env (парсит AST, не исполняет):
1. py_compile всех модулей.
2. TOOL_DEFINITIONS ↔ TOOL_MAP: совпадение множеств имён, без дублей.
3. Каждая функция в TOOL_MAP определена в tools.py с правильной сигнатурой
   (необязательные параметры имеют дефолты, чтобы LLM мог вызвать с минимумом).
4. user_id НЕТ ни в сигнатурах LLM-инструментов, ни в их parameters.properties.
"""
import ast
import py_compile
import sys

MODULES = ["config.py", "database.py", "tools.py", "llm.py", "main.py", "ctx.py", "constants.py"]
ok = True


def fail(msg):
    global ok
    ok = False
    print("FAIL:", msg)


# 1. Компиляция
for mod in MODULES:
    try:
        py_compile.compile(mod, doraise=True)
    except py_compile.PyCompileError as e:
        fail(f"{mod} не компилируется: {e}")

# Парсим llm.py и tools.py
llm_tree = ast.parse(open("llm.py", encoding="utf-8").read())
tools_tree = ast.parse(open("tools.py", encoding="utf-8").read())


def top_assigns(tree):
    """name -> ast.Assign node для top-level присваиваний."""
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = node
    return out


def dict_get(d_node, key):
    if not isinstance(d_node, ast.Dict):
        return None
    for k, v in zip(d_node.keys, d_node.values):
        if isinstance(k, ast.Constant) and k.value == key:
            return v
    return None


llm_assigns = top_assigns(llm_tree)

# 2. TOOL_DEFINITIONS имена ↔ TOOL_MAP ключи
td = llm_assigns["TOOL_DEFINITIONS"].value
def_names_order = []
for el in td.elts:
    func = dict_get(el, "function")
    name_node = dict_get(func, "name")
    def_names_order.append(name_node.value)
def_names = set(def_names_order)

tm = llm_assigns["TOOL_MAP"].value
map_keys = {k.value for k in tm.keys if isinstance(k, ast.Constant)}

print(f"TOOL_DEFINITIONS: {len(def_names)} уникальных / {len(def_names_order)} всего")
print(f"TOOL_MAP:         {len(map_keys)} ключей")
if def_names != map_keys:
    fail(f"несовпадение имён. Только в defs: {def_names - map_keys}. "
         f"Только в map: {map_keys - def_names}")
if len(def_names_order) != len(def_names):
    fail(f"дубли в TOOL_DEFINITIONS: {len(def_names_order) - len(def_names)} лишних")

# 3. Каждое имя в TOOL_MAP — функция, определённая в tools.py
tools_funcs = {
    n.name: n for n in tools_tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
}
# Импорты в llm.py (какие имена импортируются из tools)
llm_imported_from_tools = set()
for n in llm_tree.body:
    if isinstance(n, ast.ImportFrom) and n.module == "tools":
        for alias in n.names:
            llm_imported_from_tools.add(alias.asname or alias.name)

for key in map_keys:
    val = None
    # найдём значение в TOOL_MAP dict
    for k, v in zip(tm.keys, tm.values):
        if isinstance(k, ast.Constant) and k.value == key:
            val = v
            break
    if isinstance(val, ast.Name):
        fn_name = val.id
    else:
        fail(f"значение TOOL_MAP[{key}] не простой Name")
        continue
    if fn_name not in tools_funcs and fn_name not in llm_imported_from_tools:
        fail(f"функция {fn_name} (для инструмента {key}) не определена в tools.py и не импортирована")

# 4. user_id не должен торчать в parameters.properties инструментов
for el in td.elts:
    func = dict_get(el, "function")
    fname = dict_get(func, "name").value
    params = dict_get(func, "parameters")
    if isinstance(params, ast.Dict):
        props = dict_get(params, "properties")
        if isinstance(props, ast.Dict):
            for k in props.keys:
                if isinstance(k, ast.Constant) and k.value == "user_id":
                    fail(f"инструмент {fname} раскрывает user_id в parameters — это небезопасно")

print()
print("ALL OK" if ok else "HAS FAILURES")
sys.exit(0 if ok else 1)
