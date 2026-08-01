"""Tests for categorization_schema.py - JSON Schema generation for categorization.yaml."""

import json

from tally.categorization_common import format_rule_label, rule_labels
from tally.categorization_schema import build_schema


def make_rules():
    """A small synthetic rule list in the 7-tuple shape from get_all_rules."""
    return [
        ('AMAZON', 'Amazon', 'Shopping', 'Books', None, 'user', ['gift']),
        ('AMAZON', 'Amazon', 'Shopping', 'Gifts', None, 'user', []),
        ('NETFLIX', 'Netflix', 'Subscriptions', 'Streaming', None, 'user', ['entertainment', 'recurring']),
        ('VIOC', 'Valvoline', 'Auto', 'Maintenance', None, 'user', []),
        ('TRANSFER', 'Savings', '', '', None, 'user', ['transfer']),  # tag-only rule
    ]


def iter_schemas(node):
    """Yield every dict in the schema tree that looks like a (sub)schema."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from iter_schemas(value)
    elif isinstance(node, list):
        for item in node:
            yield from iter_schemas(item)


def resolve(schema, node):
    """Resolve a single-level local $ref (e.g. '#/definitions/useRuleValue')."""
    ref = node['$ref']
    assert ref.startswith('#/'), f"Only local refs are supported in this schema, got {ref}"
    target = schema
    for part in ref[2:].split('/'):
        assert part in target, f"$ref target segment '{part}' missing (full ref: {ref})"
        target = target[part]
    return target


def walk_properties(schema, node, path='root'):
    """Yield (path, resolved_property_schema) for every 'properties' entry at any depth.

    $ref properties are resolved against definitions so their description can be checked
    at the definition site rather than the use site.
    """
    if not isinstance(node, dict):
        return
    if 'properties' in node and isinstance(node['properties'], dict):
        for name, prop in node['properties'].items():
            prop_path = f"{path}.{name}"
            resolved = resolve(schema, prop) if '$ref' in prop else prop
            yield prop_path, resolved
            yield from walk_properties(schema, resolved, prop_path)
    if 'items' in node:
        yield from walk_properties(schema, node['items'], f"{path}[]")


class TestSchemaStructure:
    """Draft-07 shape and general well-formedness."""

    def test_is_json_serializable(self):
        schema = build_schema(make_rules())
        # Round-trips cleanly, so it is safe to json.dump straight to disk.
        json.loads(json.dumps(schema))

    def test_declares_draft_07(self):
        schema = build_schema(make_rules())
        assert schema['$schema'] == 'http://json-schema.org/draft-07/schema#'

    def test_top_level_shape_is_flat_no_sources_nesting(self):
        schema = build_schema(make_rules())
        assert schema['type'] == 'object'
        assert 'state' in schema['properties']
        assert 'unknowns' in schema['properties']
        assert 'sources' not in schema['properties']

    def test_run_summary_lives_under_state(self):
        schema = build_schema(make_rules())
        state = schema['properties']['state']['properties']
        assert set(state) == {'generated', 'totalSources', 'totalUnknowns', 'totalReviews'}

    def test_hints_are_not_in_the_answer_file(self):
        """Hints moved to the companion file to keep this one scannable."""
        schema = build_schema(make_rules())
        assert 'hints' not in schema['properties']['unknowns']['items']['properties']


class TestUseRuleDef:
    """definitions + $ref for the useRule enum."""

    def test_use_rule_is_a_ref(self):
        schema = build_schema(make_rules())
        use_rule = schema['properties']['unknowns']['items']['properties']['useRule']
        assert '$ref' in use_rule, "useRule should be a $ref into definitions, not an inline enum"

    def test_ref_resolves(self):
        schema = build_schema(make_rules())
        for node in iter_schemas(schema):
            if '$ref' in node:
                resolve(schema, node)  # raises via assert if it doesn't resolve

    def test_use_rule_enum_matches_rule_labels(self):
        rules = make_rules()
        schema = build_schema(rules)
        definition = schema['definitions']['useRuleValue']
        expected = [None] + rule_labels(rules)
        assert definition['enum'] == expected

    def test_use_rule_enum_values_match_format_rule_label(self):
        rules = make_rules()
        schema = build_schema(rules)
        enum_values = set(schema['definitions']['useRuleValue']['enum'])
        assert format_rule_label('Amazon', 'Shopping', 'Books', ['gift']) in enum_values
        assert format_rule_label('Netflix', 'Subscriptions', 'Streaming',
                                  ['entertainment', 'recurring']) in enum_values
        # tag-only rule: no category/subcategory clause
        assert format_rule_label('Savings', '', '', ['transfer']) in enum_values

    def test_no_empty_string_in_any_enum(self):
        schema = build_schema(make_rules())
        for node in iter_schemas(schema):
            enum = node.get('enum')
            if enum is not None:
                assert '' not in enum, f"Found empty-string enum entry in {enum!r}"

    def test_null_covers_empty_use_rule(self):
        schema = build_schema(make_rules())
        assert schema['definitions']['useRuleValue']['enum'][0] is None


class TestDescriptions:
    """Every property, at every nesting depth, must have a usable description."""

    def test_every_property_has_a_description(self):
        schema = build_schema(make_rules())
        missing = []
        for path, prop in walk_properties(schema, schema):
            description = prop.get('description')
            if not description or not description.strip():
                missing.append(path)
        assert not missing, f"Properties missing a description: {missing}"

    def test_top_level_properties_have_descriptions(self):
        schema = build_schema(make_rules())
        assert schema['properties']['state']['description']
        assert schema['properties']['unknowns']['description']

    def test_machine_owned_fields_say_so(self):
        schema = build_schema(make_rules())
        row = schema['properties']['unknowns']['items']['properties']
        for field in ('id', 'key', 'source', 'date', 'merchant', 'amount'):
            description = row[field]['description'].lower()
            assert 'machine-owned' in description or 'do not edit' in description, (
                f"'{field}' description should flag it as machine-owned: {description!r}"
            )

    def test_new_rule_says_tally_validates_nothing(self):
        schema = build_schema(make_rules())
        description = schema['properties']['unknowns']['items']['properties']['newRule']['description']
        assert 'not' in description.lower() and ('parse' in description.lower() or 'validat' in description.lower())


class TestTags:
    """tags is an array with items.enum, not a comma-separated string."""

    def test_tags_is_array_of_enum(self):
        rules = make_rules()
        schema = build_schema(rules)
        tags_schema = schema['properties']['unknowns']['items']['properties']['edits']['properties']['tags']
        assert tags_schema['type'] == 'array'
        assert 'enum' in tags_schema['items']
        assert tags_schema['items']['type'] == 'string'

    def test_tags_enum_matches_rule_facets(self):
        from tally.categorization_common import rule_facets
        rules = make_rules()
        _categories, _subcategories, expected_tags = rule_facets(rules)
        schema = build_schema(rules)
        tags_schema = schema['properties']['unknowns']['items']['properties']['edits']['properties']['tags']
        assert tags_schema['items']['enum'] == expected_tags


class TestCategoryFreeForm:
    """edits.category suggests known categories but accepts new values."""

    def test_category_has_no_restrictive_enum(self):
        schema = build_schema(make_rules())
        category_schema = schema['properties']['unknowns']['items']['properties']['edits']['properties']['category']
        assert 'enum' not in category_schema, (
            "edits.category must not use 'enum' - it would reject brand-new categories"
        )

    def test_category_offers_suggestions_via_examples(self):
        from tally.categorization_common import rule_facets
        rules = make_rules()
        categories, _subcategories, _tags = rule_facets(rules)
        schema = build_schema(rules)
        category_schema = schema['properties']['unknowns']['items']['properties']['edits']['properties']['category']
        assert category_schema['examples'] == categories

    def test_category_type_still_allows_any_string(self):
        schema = build_schema(make_rules())
        category_schema = schema['properties']['unknowns']['items']['properties']['edits']['properties']['category']
        assert 'string' in category_schema['type']
