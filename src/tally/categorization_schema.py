"""JSON-Schema (draft-07) generator for `categorization.yaml`.

The schema drives editor autocomplete (Ctrl+Space) and hover popups while a
user hand-edits the categorization review file. It is regenerated alongside
`categorization.yaml` on every `tally up` run, so it always reflects the
merchant rules currently in `merchants.rules`.

This module has exactly one public entry point: `build_schema(rules)`.
"""

from .categorization_common import rule_facets, rule_labels


def build_schema(rules):
    """Build the draft-07 JSON Schema for the generated `categorization.yaml`.

    Args:
        rules: list of 7-tuples from `merchant_utils.get_all_rules`,
            ``(pattern, merchant, category, subcategory, parsed, source, tags)``.

    Returns:
        A JSON-serializable dict — the full schema document.
    """
    categories, _subcategories, tags = rule_facets(rules)
    # No "" entry: an unanswered useRule is written bare, which parses as null,
    # and Ctrl+Space completes better from there than from inside a quote pair.
    # Adding "" would also put a blank row in the completion list.
    use_rule_values = [None] + rule_labels(rules)

    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "Tally categorization review",
        "description": (
            "Review file for transactions tally could not match to an existing "
            "merchant rule. Fill in useRule, newRule, or edits for each row, save, "
            "and tell the agent to process your answers."
        ),
        "type": "object",
        # The useRule enum is duplicated across every row in the equivalent
        # hand-rolled schema this replaces (it accounted for most of that
        # file's 1,747 lines). Defining it once under definitions and $ref-ing it
        # from the single place it is used keeps the document a fraction of
        # the size and gives one place to look when the rule list changes.
        "definitions": {
            "useRuleValue": {
                "type": ["string", "null"],
                "enum": use_rule_values,
                "description": (
                    "Label of an existing rule in merchants.rules to apply to this "
                    "transaction, formatted as \"[Merchant] Category / Subcategory | "
                    "tags: a, b\" (the tags clause is omitted when the rule has none, "
                    "and Category / Subcategory is omitted for tag-only rules). "
                    "Ctrl+Space to pick from every rule currently defined. Leave null "
                    "to categorize this row with newRule or edits instead. When set, "
                    "the agent widens that rule's match expression to also cover this "
                    "transaction and applies any CATEGORY:/TAG: tagging the rule "
                    "expects."
                ),
            },
        },
        "properties": {
            "state": {
                "type": "object",
                "description": (
                    "Summary of this run. Machine-owned — rewritten every time; "
                    "collapse it to get it out of the way."
                ),
                "properties": {
                    "generated": {
                        "type": "string",
                        "description": (
                            "ISO 8601 timestamp of the tally run that produced "
                            "this file. Compared against the source-file and "
                            "merchants.rules modification times to detect rows "
                            "whose answer was given but never actually applied."
                        ),
                    },
                    "totalSources": {
                        "type": "integer",
                        "description": (
                            "Number of distinct data sources represented in the "
                            "unknowns below — not the number of sources "
                            "configured in settings.yaml."
                        ),
                    },
                    "totalUnknowns": {
                        "type": "integer",
                        "description": (
                            "How many rows are in the unknowns list, i.e. how "
                            "many transactions still need an answer."
                        ),
                    },
                    "totalReviews": {
                        "type": "integer",
                        "description": (
                            "How many rows are awaiting confirmation of an "
                            "existing categorization rather than a new answer. "
                            "Always 0 until the review feature ships."
                        ),
                    },
                },
            },
            "reviews": {
                "type": "array",
                "description": (
                    "Transactions already categorized by a rule flagged "
                    "'review: true', awaiting your confirmation. Leave a row "
                    "untouched and its current rule stands. These persist "
                    "indefinitely — across months and files — until the file "
                    "named in 'file' is marked reviewComplete in inventory.yaml. "
                    "Ids continue the same sequence as unknowns, so referring "
                    "to a row by number is never ambiguous."
                ),
                "items": {
                    "type": "object",
                    "description": (
                        "One categorized transaction awaiting confirmation."
                    ),
                    "properties": {
                        "id": {
                            "type": "integer",
                            "description": (
                                "Sequential number continuing on from the "
                                "unknowns list. Machine-owned — renumbered "
                                "every run."
                            ),
                        },
                        "key": {
                            "type": "string",
                            "description": (
                                "Machine-owned stable identity for this "
                                "transaction. Do not edit."
                            ),
                        },
                        "source": {
                            "type": "string",
                            "description": (
                                "Data source this transaction came from. "
                                "Machine-owned, display-only — do not edit."
                            ),
                        },
                        "date": {
                            "type": "string",
                            "description": (
                                "Transaction date from the source CSV. "
                                "Machine-owned, display-only — do not edit."
                            ),
                        },
                        "merchant": {
                            "type": "string",
                            "description": (
                                "Raw merchant text from the source CSV. "
                                "Machine-owned, display-only — do not edit."
                            ),
                        },
                        "amount": {
                            "type": "string",
                            "description": (
                                "Signed amount with currency prefix. "
                                "Machine-owned, display-only — do not edit."
                            ),
                        },
                        "currently": {
                            "type": "string",
                            "description": (
                                "How this transaction is categorized right now, "
                                "by the rule that matched it. This is what "
                                "stands if you leave the row alone. "
                                "Machine-owned — do not edit."
                            ),
                        },
                        "file": {
                            "type": "string",
                            "description": (
                                "Data file this transaction came from. Set this "
                                "path's reviewComplete to true in inventory.yaml "
                                "to stop its rows appearing here. Machine-owned "
                                "— do not edit."
                            ),
                        },
                        "aiNotes": {
                            "type": ["string", "null"],
                            "description": (
                                "Your agent's notes. Agent-owned, not yours — "
                                "tally never writes or overwrites this field."
                            ),
                        },
                        "useRule": {"$ref": "#/definitions/useRuleValue"},
                        "newRule": {
                            "type": ["string", "null"],
                            "description": (
                                "Free-form prose describing a correction. "
                                "Tally does not parse or validate this field."
                            ),
                        },
                        "edits": {
                            "type": "object",
                            "description": (
                                "Direct overrides for just this transaction, "
                                "if the existing rule got it wrong."
                            ),
                            "properties": {
                                "category": {
                                    "type": ["string", "null"],
                                    "examples": categories,
                                    "description": (
                                        "Corrected Category / Subcategory. Any "
                                        "value is accepted."
                                    ),
                                },
                                "tags": {
                                    "type": "array",
                                    "description": "Corrected tags.",
                                    "items": {
                                        "type": "string",
                                        "enum": tags,
                                        "description": (
                                            "Ctrl+Space to pick from every tag "
                                            "in merchants.rules."
                                        ),
                                    },
                                },
                                "memo": {
                                    "type": ["string", "null"],
                                    "description": (
                                        "Optional memo text for this "
                                        "transaction."
                                    ),
                                },
                            },
                        },
                    },
                },
            },
            "unknowns": {
                "type": "array",
                "description": (
                    "One row per transaction tally could not match to an existing "
                    "rule, sorted by source ascending then date descending. Rows "
                    "whose transaction matches a rule by the next run are dropped; "
                    "rows still unmatched carry forward whatever you filled in here "
                    "(useRule, newRule, edits, aiNotes) even though `id` is "
                    "renumbered."
                ),
                "items": {
                    "type": "object",
                    "description": (
                        "One uncategorized transaction, plus your answer for how it "
                        "should be categorized."
                    ),
                    "properties": {
                        "id": {
                            "type": "integer",
                            "description": (
                                "Sequential number for this row, used only to refer "
                                "to it in chat (e.g. \"process 7, 9, 13\"). "
                                "Machine-owned — renumbered from 1 on every "
                                "regeneration so it always matches what is on "
                                "screen; do not expect it to be stable across runs."
                            ),
                        },
                        "key": {
                            "type": "string",
                            "description": (
                                "Machine-owned identity for this row: "
                                "sha1(source + date + amount + raw merchant text) "
                                "truncated to 8 characters, with a numeric ordinal "
                                "appended for exact duplicate transactions. Used "
                                "internally to reattach your answer to the same "
                                "transaction across regenerations. Do not edit — "
                                "changing it orphans whatever you filled in for "
                                "this row."
                            ),
                        },
                        "source": {
                            "type": "string",
                            "description": (
                                "Name of the data source (as configured in "
                                "settings.yaml) this transaction came from. "
                                "Machine-owned, display-only — do not edit."
                            ),
                        },
                        "date": {
                            "type": "string",
                            "description": (
                                "Transaction date exactly as it appears in the "
                                "source CSV. Machine-owned, display-only — do not "
                                "edit."
                            ),
                        },
                        "merchant": {
                            "type": "string",
                            "description": (
                                "Raw merchant/description text exactly as it "
                                "appears in the source CSV, before normalization. "
                                "Machine-owned, display-only — do not edit."
                            ),
                        },
                        "amount": {
                            "type": "string",
                            "description": (
                                "Signed transaction amount with currency prefix "
                                "(e.g. \"+$112.45\"), formatted for display. "
                                "Machine-owned, display-only — do not edit."
                            ),
                        },
                        "aiNotes": {
                            "type": ["string", "null"],
                            "description": (
                                "Your agent's notes on this row — its best guess "
                                "and reasoning. Agent-owned, not yours: a blank "
                                "value is not a question waiting on you. Say "
                                "\"annotate\" to have it filled in. Tally never "
                                "writes or overwrites this field, and preserves "
                                "it verbatim across regenerations while the row "
                                "remains unresolved."
                            ),
                        },
                        "useRule": {
                            "$ref": "#/definitions/useRuleValue",
                        },
                        "newRule": {
                            "type": ["string", "null"],
                            "description": (
                                "Free-form prose describing a brand-new rule to "
                                "create, e.g. \"new rule: Category Shopping / "
                                "Books, tag gift\". Purely agent-side — tally "
                                "does not parse or validate this field at all; "
                                "the agent reading this file back interprets "
                                "whatever you write here and creates the rule "
                                "in merchants.rules."
                            ),
                        },
                        "edits": {
                            "type": "object",
                            "description": (
                                "Direct, one-off overrides for just this "
                                "transaction, for when no existing or new rule "
                                "is worth creating."
                            ),
                            "properties": {
                                "category": {
                                    "type": ["string", "null"],
                                    # A plain "enum" here would reject any category not
                                    # already in merchants.rules, but the user may be
                                    # introducing a brand-new category. "examples" is
                                    # the JSON-Schema keyword editors (the VS Code/
                                    # redhat YAML language server included) use to
                                    # populate Ctrl+Space suggestions WITHOUT
                                    # restricting the value like "enum" would — it
                                    # documents likely values without validating
                                    # against them, so any string still passes.
                                    "examples": categories,
                                    "description": (
                                        "Category / Subcategory to apply to just "
                                        "this transaction (e.g. \"Shopping / "
                                        "Books\"). Ctrl+Space suggests existing "
                                        "categories, but any value is accepted "
                                        "— type a new one if you're introducing "
                                        "a category that doesn't exist yet."
                                    ),
                                },
                                "tags": {
                                    "type": "array",
                                    "description": (
                                        "Tags to apply to just this transaction. "
                                        "Each entry adds a `TAG: x` marker to the "
                                        "CSV tagging column. An empty or omitted "
                                        "list means no tags."
                                    ),
                                    "items": {
                                        "type": "string",
                                        "enum": tags,
                                        "description": (
                                            "Ctrl+Space to pick from every tag "
                                            "currently used across "
                                            "merchants.rules."
                                        ),
                                    },
                                },
                                "memo": {
                                    "type": ["string", "null"],
                                    "description": (
                                        "Optional free text to write into the "
                                        "memo column of the source CSV for this "
                                        "transaction."
                                    ),
                                },
                            },
                        },
                    },
                },
            },
        },
    }
