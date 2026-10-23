# -*- coding: utf-8 -*-
"""
Read and write managed-folder files in a way that works in containers.

Symptom
-------
Python recipes saved a fitted scaler and an Isolation Forest model as
pickles into a managed folder, and a later scoring recipe loaded them.
On the design node they ran on local DSS compute. After the project was
deployed to an automation node, the recipes ran with containerized
execution on EKS and the steps that touched the managed folder failed.
The team believed it had worked in containers before, so something had
changed. Someone suggested "use streaming".

Cause
-----
The recipes used `folder.get_path()` and then opened files with
`joblib.load(os.path.join(path, name))`. `get_path()` returns a path on
the DSS host's disk. Inside the recipe's pod that path does not exist
or points at nothing. Other things that produce the same symptom and
are worth ruling out when you have the error text:
  * the folder's storage backend changed during the move (for example
    filesystem to S3), which breaks get_path() access entirely;
  * the folder's connection is not configured on the automation node.
We never saw the error message for this case.

Fix
---
Go through the Dataiku API instead of the filesystem:
    folder.get_download_stream(name)   to read
    folder.get_upload_stream(name)     to write
It is a mechanical swap: find every get_path() in the affected recipes
and replace it. Check the upstream recipe that writes the files too. It
usually has the same problem, and if it has been failing, the files in
the folder may be stale.

The version handed to the team loaded straight from the stream:

    with folder.get_download_stream("scaler.pkl") as stream:
        scaler = joblib.load(stream)

Whether that exact form ran cleanly on their side was not confirmed.
This script buffers the stream into BytesIO first, because the download
stream is not seekable and joblib/pickle may need to seek (joblib peeks
at the header to detect compression). Buffering costs memory equal to
the file size, which is fine for model files. For very large files,
stream to a temp file instead.

The `os` and `io` imports that only served get_path() can be removed
from the original recipe. io is used here for the buffer.

Run as the body of a Python recipe whose inputs include the managed
folder. Replace <managed_folder_id> with the folder id or name.
"""
import io
import pickle

import dataiku
import joblib

FOLDER_ID = "<managed_folder_id>"
folder = dataiku.Folder(FOLDER_ID)


# ---- Before (breaks in containers) ----
# folder_path = folder.get_path()
# scaler = joblib.load(os.path.join(folder_path, "scaler.pkl"))
# model = joblib.load(os.path.join(folder_path, "model.pkl"))


def load_joblib(name):
    """Read a joblib file from the managed folder through the API."""
    with folder.get_download_stream(name) as stream:
        return joblib.load(io.BytesIO(stream.read()))


def save_joblib(obj, name):
    """Write an object to the managed folder through the API."""
    buf = io.BytesIO()
    joblib.dump(obj, buf)
    buf.seek(0)
    with folder.get_upload_stream(name) as out:
        out.write(buf.read())


def load_pickle(name):
    """Plain pickle variant."""
    with folder.get_download_stream(name) as stream:
        return pickle.load(io.BytesIO(stream.read()))


def save_pickle(obj, name):
    """Plain pickle variant. pickle.dump can write straight to the upload stream."""
    with folder.get_upload_stream(name) as out:
        pickle.dump(obj, out)


if __name__ == "__main__":
    # Scoring recipe: load what the training recipe saved.
    scaler = load_joblib("scaler.pkl")
    model = load_joblib("model.pkl")
    print("Loaded", type(scaler).__name__, "and", type(model).__name__)

    # Training recipe would instead end with:
    # save_joblib(scaler, "scaler.pkl")
    # save_joblib(model, "model.pkl")
