# SPDX-License-Identifier: Apache-2.0
"""Canonical lesson-file JSON Schema (PRODUCT.md §9).

This is the single source of truth. The converter writes it to ``lessons/schema.yaml``
and the loader validates every converted file against it. It is reproduced verbatim from
§9 except for one documented relaxation (see docs/DECISIONS.md): ``redirect_line.maxLength``
is 600 rather than 250 because the authored redirect lines run ~340-360 characters and are
preserved verbatim.
"""

from __future__ import annotations

SCHEMA_YAML = r"""# lessons/schema.yaml — single source of truth for loader validation
$schema: "http://json-schema.org/draft-07/schema#"
$defs:

  curriculum:                       # validates lessons/curriculum.yaml
    type: object
    required: [course, version, lessons]
    additionalProperties: false
    properties:
      course:  { type: string, minLength: 1 }
      version: { type: integer, minimum: 1 }
      lessons:                      # order = catalogue order
        type: array
        minItems: 1
        items: { type: string, pattern: "^[a-z0-9_]+/lesson\\.ya?ml$" }

  lesson:                           # validates each <id>/lesson.yaml
    type: object
    required: [id, title, difficulty, teach_file, card_file, steps,
               commands, chaos_options, observe, qna, meta]
    additionalProperties: false
    properties:
      id:         { type: string, pattern: "^[a-z0-9_]+$" }
      title:      { type: string, minLength: 1 }
      difficulty: { enum: [intro, core, advanced] }
      requires:   { type: array, items: { type: string }, default: [] }
      teach_file: { type: string, pattern: "\\.md$" }        # must resolve in lesson folder
      card_file:  { type: string, pattern: "\\.ya?ml$" }
      demo_answers_file: { type: string }                     # optional
      steps:
        type: array
        minItems: 3
        items:
          type: object
          required: [id, kind, title]
          additionalProperties: false
          properties:
            id:       { type: string }
            kind:     { enum: [teach, observe, qna, chaos_select, restore] }
            title:    { type: string }
            optional: { type: boolean, default: false }       # core vs optional scenario
      commands:
        type: object
        required: [baseline, after_chaos, vocabulary]
        additionalProperties: false
        properties:
          baseline:    { type: array, minItems: 1, items: { type: string } }
          after_chaos: { type: array, minItems: 1, items: { type: string } }
          per_step:    { type: object,                        # observe-step-id -> commands
                         additionalProperties: { type: array, minItems: 1, items: { type: string } } }
          vocabulary:  { type: array, minItems: 3, items: { type: string } }  # experiment globs
      chaos_options:
        type: array
        minItems: 1
        items:
          type: object
          required: [id, label, type, inject, restore, expected_effects, risk, enabled]
          additionalProperties: false
          properties:
            id:               { type: string, pattern: "^[a-z0-9_]+$" }
            label:            { type: string }
            type:             { enum: [link, config, service, churn] }
            inject:           { type: array, minItems: 1, items: { type: string } }
            restore:          { type: array, minItems: 1, items: { type: string } }
            expected_effects: { type: array, minItems: 1, items: { type: string } }
            risk:             { enum: [low, medium, high] }
            enabled:          { type: boolean }               # unverified options ship false
            plan_b:           { type: string }                # optional fallback variant
      observe:
        type: object
        required: [facts]
        additionalProperties: false
        properties:
          facts:
            type: array
            minItems: 1
            items: { enum: [interface_status, mac_table, lldp_neighbors, vlan_membership,
                            bgp_neighbors, route_count, ping_loss, redis_keys, propagation_lag] }
          measure_recovery: { type: boolean, default: false }
          key_fields:       { type: array, items: { type: string } }
      qna:
        type: object
        required: [scope_keywords, suggested_questions]
        additionalProperties: false
        properties:
          scope_keywords: { type: array, minItems: 5, items: { type: string } }
          suggested_questions:                                # qna-step-id -> exactly 3
            type: object
            additionalProperties:
              type: array
              minItems: 3
              maxItems: 3
              items: { type: string }
      meta:
        type: object
        required: [verified_on, card_version]
        properties:
          author:       { type: string }
          verified_on:  { type: string }                      # image tag or "UNVERIFIED — ..."
          card_version: { type: integer, minimum: 1 }         # bump invalidates demo answers

  card:                             # validates each <id>/card.yaml
    type: object
    required: [objective, in_scope_subtopics, key_concepts, command_field_meanings,
               healthy_state_expectations, expected_chaos_effects, common_misconceptions,
               out_of_scope, redirect_line]
    additionalProperties: false
    properties:
      source_urls:        { type: array, items: { type: string } }
      objective:          { type: string }
      in_scope_subtopics: { type: array, minItems: 3, items: { type: string } }
      key_concepts:       { type: array, minItems: 5, maxItems: 10, items: { type: string } }
      command_field_meanings:                                  # command -> field -> meaning
        type: object
        additionalProperties: { type: object, additionalProperties: { type: string } }
      healthy_state_expectations: { type: string }             # in WORDS — never fabricated output
      expected_chaos_effects:                                  # keys must equal chaos ids
        type: object
        additionalProperties: { type: string }
      common_misconceptions:
        type: array
        minItems: 5
        items:
          type: object
          required: [misconception, why_wrong, correct_model]
          properties:
            misconception: { type: string }
            why_wrong:     { type: string }
            correct_model: { type: string }
      out_of_scope:  { type: array, minItems: 1, items: { type: string } }
      redirect_line: { type: string, maxLength: 600 }          # §9 says 250; relaxed (see DECISIONS.md)
"""
