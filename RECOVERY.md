#### What to back up

| What | Where | Why |
| --- | --- | --- |
| Document library | `RAG_DATA_DIR` (documents.yaml, raw/, curated/, index/) | the documents and the search index |
| Accounts, logs, settings, test set, restore points | `RAG_STATE_DIR` | everything the app writes |
| Settings | environment variables (see `rag/config.py`) and `.streamlit/config.toml` | how the app is configured |
| App version | the Git tag or Docker image tag (shown under System) | to go back to a working version |

Back up both folders every night, and keep at least 7 days. With Docker, both folders are on the `./state` volume.

#### A document update went wrong

1. **Backups & recovery → Restore points**: pick the point taken *before* the change and click **Restore**. The
   current state is saved as a restore point first, so a restore can itself be undone.
2. The policy owner reruns the test set (**Test set → Run the test set now**) and checks there are no wrong answers.

The app also undoes a failed upload by itself: the previous version stays in use, and the policy owner sees the error.

#### An app update went wrong

1. Go back to the previous version: `git checkout <previous tag>` (or start the previous Docker image tag).
2. Restart the app. Accounts, documents and logs are in the data folders, so they are kept.
3. If the search index does not load, rebuild it: **System → Rebuild the search index** (or `python -m rag.ingest`).

#### The server was lost

1. Install the app (README, "Quick start" or Docker) with the same app version.
2. Restore the two backed-up folders and set `RAG_DATA_DIR` / `RAG_STATE_DIR` to them.
3. Start the app, sign in as IT, and check **System** and the **on-premise check**.

#### Locked out

- An IT administrator forgot their password: on the server, `python -m rag.auth reset <username>`.
- No IT administrator is left: `python -m rag.auth create-admin <username>`.
- No document editor is left: `python -m rag.auth add <username> --role policy_owner --editor`.
