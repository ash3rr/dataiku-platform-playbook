# -*- coding: utf-8 -*-
"""
List recipes in a project that override the container execution setting.

Used to rule out per-recipe overrides when a project with an EKS default
still ran recipes locally. On our project (hundreds of recipes) it printed
nothing, meaning every recipe inherited, which pointed at the code env
(see check_code_env_container_status.py).

Gotcha: recipe.get_settings() returns a type-specific object
(e.g. PrepareRecipeSettings) that has no get_raw():
    AttributeError: 'PrepareRecipeSettings' object has no attribute 'get_raw'
get_definition_and_payload().get_recipe() works for every recipe type.

That executionParams is the right key for the container override was not
independently confirmed: an empty result is consistent with "no overrides"
but would also be what a wrong key gives. Spot-check one recipe you know
has an override before trusting a blank run.
"""
import dataiku

PROJECT_KEY = "<PROJECT_KEY>"

client = dataiku.api_client()
project = client.get_project(PROJECT_KEY)

for recipe in project.list_recipes():
    r = project.get_recipe(recipe['name'])
    raw = r.get_definition_and_payload()
    exec_env = raw.get_recipe().get('params', {}).get('executionParams', {})
    if exec_env:
        print(recipe['name'], exec_env)

print("done")
