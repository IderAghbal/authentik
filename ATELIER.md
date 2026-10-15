# Atelier's carry on authentik

`atelier/main` is authentik `version/2026.2.1` with the fixes below merged
in, one branch each. The image Atelier runs copies in the Python files
`atelier/main` changes against the release tag
(`infrastructure/k8s/tools/authentik` in Atelier's repository). Nothing
here goes upstream.

## Branches

Each branch is cut from `version/2026.2.1`, carries one fix and its test in
authentik's own layout, and is merged into `atelier/main` with `--no-ff`.

| Branch | Why it exists | Test |
| --- | --- | --- |
| `fix/scim-source-namespaces` | A SCIM source resolved users and groups by global name, so one source could take over, deactivate or delete users and groups it did not create. A source now resolves through its own connections only, names what it creates `<slug>.<name>`, keeps the client's names in responses, and a migration holds each user and group to one source. | `sources/scim/tests/test_namespaces.py` |
| `fix/scim-user-conflict-message` | The user endpoint's 409 said "Group with ID exists already." | `sources/scim/tests/test_user_conflict_message.py` |
| `fix/scim-source-member-scoping` | Group members resolved among every user of the instance, so a source could seat users it did not create. | `sources/scim/tests/test_member_scoping.py` |
| `fix/scim-patch-deep-copy` | A PATCH to a nested or multi-valued attribute answered 200 and changed nothing. | `sources/scim/tests/test_patch_nested.py` |
| `fix/scim-group-member-filter-remove` | Removing a member by `members[value eq "<id>"]` removed nobody. | `sources/scim/tests/test_group_member_filter_remove.py` |
| `fix/scim-filters` | `externalId eq` and unparsable filters answered 500, and an unknown attribute widened an `and`. | `sources/scim/tests/test_filters.py` |
| `fix/scim-never-returned` | A password sent by the client was stored and returned. | `sources/scim/tests/test_never_returned.py` |
| `fix/scim-error-responses` | Errors under a source rendered the HTML error page. | `sources/scim/tests/test_error_responses.py` |
| `fix/scim-provider-static-scope` | The SCIM provider ran the policy engine once per user of the instance on every page of a full sync. | `providers/scim/tests/test_static_scope.py` |
| `fix/scim-source-token-scope` | A SCIM source's token authenticated every endpoint of the API as the source's service account. | `sources/scim/tests/test_token_scope.py` |
| `fix/scim-provider-full-sync-queue` | A change to one user or group waited behind a full sync's queued pages, for most of the sync. | `providers/scim/tests/test_sync_queue.py` |
| `fix/outgoing-sync-rerun` | A full sync requested while another ran, as a provider save starts one, was dropped. | `providers/scim/tests/test_sync_rerun.py` |

## Moving to the next release

1. Rebase each `fix/*` branch onto the new release tag, resolve, and run
   its test on the branch: it fails on the tag and passes on the branch.
2. **The migration:** `sources/scim/migrations/fork_0001_one_source_per_object.py`
   depends on the newest migration of `authentik_sources_scim` the release
   ships. Point its dependency at the new release's newest one, so the app
   keeps one leaf.
3. Merge the new tag into `atelier/main`, then each rebased branch with
   `--no-ff`, each conflict resolved to the branch. `atelier/main` is never
   rebuilt or force-pushed: the image records the commit it was built from.
4. Regenerate the image's patches from the new `atelier/main` and run its
   suite in Atelier's repository (`infrastructure/k8s/tools/authentik`,
   `regen.py` then `tests/run.py matrix`) before the image moves.
