#!/usr/bin/env python3
"""Parse tally merchant rules and import into Actual Budget database."""
import uuid
import json
import re
import sqlite3
import sys

DB_PATH = "/Users/jeffreysedlak/Documents/Actual/My-Finances---copy-fb7664f/db.sqlite"
RULES_PATH = "/Users/jeffreysedlak/tally/config/merchants.rules"

# ── Parse rules file ──────────────────────────────────────────

def parse_rules_file(path):
    rules = []
    current = None
    with open(path) as f:
        for line in f:
            line = line.rstrip()
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                if current and current.get('match'):
                    rules.append(current)
                    current = None
                continue
            m = re.match(r'^\[(.+)\]$', stripped)
            if m:
                if current and current.get('match'):
                    rules.append(current)
                current = {'name': m.group(1), 'match': '', 'category': '', 'subcategory': '', 'tags': ''}
                continue
            if current is not None:
                m = re.match(r'^(\w+):\s*(.+)$', stripped)
                if m:
                    current[m.group(1)] = m.group(2).strip()
    if current and current.get('match'):
        rules.append(current)
    return rules

def strip_inline_comment(s):
    in_quote = False
    for i, ch in enumerate(s):
        if ch == '"':
            in_quote = not in_quote
        elif ch == '#' and not in_quote:
            return s[:i].rstrip()
    return s

# ── Tokenizer ─────────────────────────────────────────────────

class Token:
    def __init__(self, type, value):
        self.type = type
        self.value = value
    def __repr__(self):
        return f"Token({self.type}, {self.value!r})"

def tokenize(expr):
    tokens = []
    i = 0
    while i < len(expr):
        if expr[i].isspace():
            i += 1
            continue
        if expr[i] == '"':
            j = i + 1
            while j < len(expr) and expr[j] != '"':
                if expr[j] == '\\':
                    j += 1
                j += 1
            tokens.append(Token('STRING', expr[i+1:j]))
            i = j + 1
            continue
        if expr[i].isdigit() or (expr[i] == '-' and i+1 < len(expr) and expr[i+1].isdigit()):
            j = i + (1 if expr[i] == '-' else 0)
            while j < len(expr) and (expr[j].isdigit() or expr[j] == '.'):
                j += 1
            tokens.append(Token('NUMBER', float(expr[i:j])))
            i = j
            continue
        if expr[i:i+2] in ('>=', '<=', '==', '!='):
            tokens.append(Token('CMP', expr[i:i+2]))
            i += 2
            continue
        if expr[i] in ('>', '<'):
            tokens.append(Token('CMP', expr[i]))
            i += 1
            continue
        if expr[i] == '(':
            tokens.append(Token('LPAREN', '('))
            i += 1
            continue
        if expr[i] == ')':
            tokens.append(Token('RPAREN', ')'))
            i += 1
            continue
        if expr[i] == ',':
            tokens.append(Token('COMMA', ','))
            i += 1
            continue
        if expr[i].isalpha() or expr[i] == '_':
            j = i
            while j < len(expr) and (expr[j].isalnum() or expr[j] == '_'):
                j += 1
            word = expr[i:j]
            if word in ('and', 'or', 'not'):
                tokens.append(Token(word.upper(), word))
            elif word in ('contains', 'startswith', 'regex', 'anyof', 'normalized', 'fuzzy'):
                tokens.append(Token('FUNC', word))
            elif word in ('amount', 'month', 'year', 'day', 'weekday', 'date'):
                tokens.append(Token('FIELD', word))
            else:
                tokens.append(Token('IDENT', word))
            i = j
            continue
        i += 1
    return tokens

# ── AST nodes ─────────────────────────────────────────────────

class Condition:
    def __init__(self, op, field, value, negated=False):
        self.op = op
        self.field = field
        self.value = value
        self.negated = negated
        self.cmp_op = None

class BoolExpr:
    def __init__(self, op, children):
        self.op = op
        self.children = children

# ── Parser ────────────────────────────────────────────────────

class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def consume(self, expected=None):
        tok = self.peek()
        if tok is None:
            raise ValueError("Unexpected end of expression")
        if expected and tok.type != expected:
            raise ValueError(f"Expected {expected}, got {tok.type} ({tok.value!r})")
        self.pos += 1
        return tok

    def parse(self):
        return self.parse_or()

    def parse_or(self):
        left = self.parse_and()
        children = [left]
        while self.peek() and self.peek().type == 'OR':
            self.consume('OR')
            children.append(self.parse_and())
        return children[0] if len(children) == 1 else BoolExpr('or', children)

    def parse_and(self):
        left = self.parse_not()
        children = [left]
        while self.peek() and self.peek().type == 'AND':
            self.consume('AND')
            children.append(self.parse_not())
        return children[0] if len(children) == 1 else BoolExpr('and', children)

    def parse_not(self):
        if self.peek() and self.peek().type == 'NOT':
            self.consume('NOT')
            child = self.parse_atom()
            if isinstance(child, Condition):
                child.negated = True
            return child
        return self.parse_atom()

    def parse_atom(self):
        tok = self.peek()
        if tok is None:
            raise ValueError("Unexpected end of expression in atom")

        if tok.type == 'LPAREN':
            self.consume('LPAREN')
            expr = self.parse_or()
            self.consume('RPAREN')
            return expr

        if tok.type == 'FUNC':
            name = tok.value
            self.consume('FUNC')
            self.consume('LPAREN')
            if name == 'contains':
                val = self.consume('STRING').value
                self.consume('RPAREN')
                return Condition('contains', 'notes', val)
            elif name == 'startswith':
                val = self.consume('STRING').value
                self.consume('RPAREN')
                return Condition('startswith', 'notes', val)
            elif name == 'regex':
                val = self.consume('STRING').value
                self.consume('RPAREN')
                return Condition('regex', 'notes', val)
            elif name in ('anyof', 'normalized', 'fuzzy'):
                values = []
                while self.peek() and self.peek().type != 'RPAREN':
                    if self.peek().type == 'COMMA':
                        self.consume('COMMA')
                        continue
                    if self.peek().type == 'NUMBER':
                        self.consume('NUMBER')  # skip threshold
                        continue
                    values.append(self.consume('STRING').value)
                self.consume('RPAREN')
                if name == 'anyof':
                    return Condition('anyof', 'notes', values)
                return Condition('contains', 'notes', values[0] if values else '')

        if tok.type == 'FIELD':
            field = tok.value
            self.consume('FIELD')
            cmp = self.consume('CMP')
            if self.peek() and self.peek().type == 'STRING':
                val = self.consume('STRING').value
            else:
                val = self.consume('NUMBER').value
            cond = Condition('cmp', field, val)
            cond.cmp_op = cmp.value
            return cond

        raise ValueError(f"Unexpected token: {tok}")

# ── Convert AST to Actual Budget conditions ───────────────────

def condition_to_actual(cond):
    if cond.op == 'contains':
        return {
            'field': 'notes', 'type': 'string',
            'op': 'doesNotContain' if cond.negated else 'contains',
            'value': cond.value
        }
    elif cond.op == 'startswith':
        return {
            'field': 'notes', 'type': 'string',
            'op': 'matches',
            'value': '^' + re.escape(cond.value)
        }
    elif cond.op == 'regex':
        return {
            'field': 'notes', 'type': 'string',
            'op': 'matches', 'value': cond.value
        }
    elif cond.op == 'anyof':
        return {
            'field': 'notes', 'type': 'string',
            'op': 'oneOf', 'value': cond.value
        }
    elif cond.op == 'cmp':
        cmp_map = {'>': 'gt', '>=': 'gte', '<': 'lt', '<=': 'lte', '==': 'is'}
        value = cond.value
        if cond.field == 'amount':
            value = int(float(value) * 100)
        return {
            'field': cond.field, 'type': 'number',
            'op': cmp_map.get(cond.cmp_op, 'is'), 'value': value
        }
    return None

def generate_rule_entries(ast):
    """Convert AST into list of (conditions_op, [condition_dicts])."""
    if isinstance(ast, Condition):
        ac = condition_to_actual(ast)
        return [('and', [ac])] if ac else []

    if not isinstance(ast, BoolExpr):
        return []

    if ast.op == 'or':
        simple = []
        complex_branches = []
        for child in ast.children:
            if isinstance(child, Condition):
                ac = condition_to_actual(child)
                if ac:
                    simple.append(ac)
            else:
                complex_branches.append(child)
        results = []
        if simple:
            results.append(('or' if len(simple) > 1 else 'and', simple))
        for branch in complex_branches:
            results.extend(generate_rule_entries(branch))
        return results

    if ast.op == 'and':
        leaves = []
        complex_children = []
        for child in ast.children:
            if isinstance(child, Condition):
                leaves.append(child)
            else:
                complex_children.append(child)
        base = [c for c in (condition_to_actual(l) for l in leaves) if c]

        if not complex_children:
            return [('and', base)]

        # Distribute AND over a single OR child
        if len(complex_children) == 1 and isinstance(complex_children[0], BoolExpr) and complex_children[0].op == 'or':
            results = []
            for or_child in complex_children[0].children:
                if isinstance(or_child, Condition):
                    ac = condition_to_actual(or_child)
                    if ac:
                        results.append(('and', base + [ac]))
                else:
                    for _, conds in generate_rule_entries(or_child):
                        results.append(('and', base + conds))
            return results

        # Fallback: flatten everything into AND
        for child in complex_children:
            for _, conds in generate_rule_entries(child):
                base.extend(conds)
        return [('and', base)]

    return []

# ── Main ──────────────────────────────────────────────────────

def main():
    print("Parsing rules file...")
    rules = parse_rules_file(RULES_PATH)
    print(f"Found {len(rules)} active rules")

    # Collect unique categories and subcategories
    cat_map = {}
    for r in rules:
        cat = r.get('category', '').strip()
        sub = r.get('subcategory', '').strip()
        if cat:
            cat_map.setdefault(cat, set())
            if sub:
                cat_map[cat].add(sub)

    print(f"Found {len(cat_map)} categories with {sum(len(v) for v in cat_map.values())} subcategories")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Existing data
    cur.execute("SELECT id, name FROM category_groups WHERE tombstone=0")
    existing_groups = {row[1]: row[0] for row in cur.fetchall()}

    cur.execute("SELECT id, name, cat_group FROM categories WHERE tombstone=0")
    existing_cats = {}
    for cid, name, gid in cur.fetchall():
        existing_cats[(name, gid)] = cid

    cur.execute("SELECT COALESCE(MAX(sort_order), 0) FROM category_groups WHERE tombstone=0")
    gsort = cur.fetchone()[0]
    cur.execute("SELECT COALESCE(MAX(sort_order), 0) FROM categories WHERE tombstone=0")
    csort = cur.fetchone()[0]

    # Create category groups and categories
    group_ids = {}
    cat_ids = {}
    groups_created = 0
    cats_created = 0

    for cat_name in sorted(cat_map.keys()):
        if cat_name in existing_groups:
            group_ids[cat_name] = existing_groups[cat_name]
        else:
            gid = str(uuid.uuid4())
            gsort += 16384
            is_income = 1 if cat_name.lower() == 'income' else 0
            cur.execute(
                "INSERT INTO category_groups (id, name, is_income, sort_order, tombstone, hidden) VALUES (?, ?, ?, ?, 0, 0)",
                (gid, cat_name, is_income, gsort))
            group_ids[cat_name] = gid
            groups_created += 1
            print(f"  + Group: {cat_name}")

        for sub_name in sorted(cat_map[cat_name]):
            gid = group_ids[cat_name]
            if (sub_name, gid) in existing_cats:
                cat_ids[(cat_name, sub_name)] = existing_cats[(sub_name, gid)]
            else:
                cid = str(uuid.uuid4())
                csort += 16384
                cur.execute(
                    "INSERT INTO categories (id, name, is_income, cat_group, sort_order, tombstone, hidden) VALUES (?, ?, 0, ?, ?, 0, 0)",
                    (cid, sub_name, gid, csort))
                cat_ids[(cat_name, sub_name)] = cid
                cats_created += 1
                print(f"    + Category: {sub_name} (under {cat_name})")

    # Add category_mapping entries (each category maps to itself)
    cur.execute("INSERT OR IGNORE INTO category_mapping (id, transferId) SELECT id, id FROM categories WHERE tombstone=0")
    mappings_created = cur.rowcount


    # Create rules
    rules_created = 0
    rules_failed = 0

    for rule in rules:
        cat = rule.get('category', '').strip()
        sub = rule.get('subcategory', '').strip()
        tags = rule.get('tags', '').strip()
        match_expr = strip_inline_comment(rule.get('match', '').strip())
        name = rule.get('name', '')

        if not match_expr or not cat or not sub:
            rules_failed += 1
            continue

        cat_key = (cat, sub)
        if cat_key not in cat_ids:
            print(f"  ! Category ({cat}, {sub}) not found for '{name}'")
            rules_failed += 1
            continue

        target_id = cat_ids[cat_key]

        try:
            tokens = tokenize(match_expr)
            ast = Parser(tokens).parse()
            entries = generate_rule_entries(ast)
        except Exception as e:
            print(f"  ! Parse failed for '{name}': {e}")
            rules_failed += 1
            continue

        for conditions_op, conditions in entries:
            if not conditions:
                continue
            actions = [{
                "op": "set", "field": "category",
                "value": target_id, "type": "id",
                "options": {"splitIndex": 0}
            }]
            if tags:
                tag_str = ' '.join(f'#{t.strip()}' for t in tags.split(',') if t.strip())
                actions.append({
                    "op": "append-notes", "field": "description",
                    "value": tag_str, "type": "id",
                    "options": {"splitIndex": 0}
                })

            rid = str(uuid.uuid4())
            cur.execute(
                "INSERT INTO rules (id, stage, conditions, actions, tombstone, conditions_op) VALUES (?, '', ?, ?, 0, ?)",
                (rid, json.dumps(conditions), json.dumps(actions), conditions_op))
            rules_created += 1

    conn.commit()
    conn.close()

    print(f"\n{'='*40}")
    print(f"Category groups created: {groups_created}")
    print(f"Categories created:      {cats_created}")
    print(f"Rules created:           {rules_created}")
    print(f"Rules skipped/failed:    {rules_failed}")
    print(f"Category mappings added: {mappings_created}")

if __name__ == '__main__':
    main()
