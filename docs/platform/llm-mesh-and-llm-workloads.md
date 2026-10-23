# LLM connections and LLM workloads

How LLM work ran on this platform, what it needed from the infrastructure, and the connection problems we hit. Dataiku does not host or train LLMs itself. It orchestrates calls to models behind LLM connections (the LLM Mesh). On this instance those were Azure OpenAI connections reached over private link.

## Where an LLM recipe runs

An LLM-based topic-clustering and document-labelling flow is the reference case. Its labelling recipe ran containerized on EKS (`dataiku.container.exec_py_recipe` in the logs, node in eu-west-1) and labelled a few hundred document clusters one at a time, sequentially, over more than two hours. Each label was an HTTP POST from the pod to the DSS backend's LLM endpoint (`/llms/completions`, served by the backend on port 10001 on the design node), which proxies to the configured provider.

What that means for sizing:

* The pod is an HTTP client doing JSON parsing. Its RSS was about 162 to 164 MB and flat across the whole run. No GPU, modest CPU and memory.
* The `vmRSSTotalMBS` figure in the resource log climbed from about 185k to 853k over the run. That is a cumulative counter across child processes since the job started, not current memory pressure.
* If the model were served locally on the same node (it was not), that process would be the one to watch, and it would not show in the recipe's logs.
* Sequential calls are the bottleneck. When the same recipe had run on local DSS compute earlier (because its code env image was never built, see [compute-and-cost.md](../architecture/compute-and-cost.md#silent-fallback-to-local)), the same hours-long serial loop applied. Parallelising the calls inside the recipe matters more than where the pod runs.

One failure mode worth knowing:

```
LLM response hit maxOutputTokens before valid JSON was produced
```

Partway through the run, the response for one cluster was cut off at the output token limit before the JSON was complete. The recipe logged it, skipped the cluster and continued, so that cluster's label was missing from the output. Causes to check: the output token budget in the prompt or LLM settings is too small for the requested JSON, or throttling at the provider. Check `maxOutputTokens` when copying such a flow to another project.

## When a GPU is needed

* Calling an LLM through a connection: no.
* Local embeddings (sentence-transformers), topic models with local embedding steps (bertopic, umap-learn, hdbscan), torch training or fine-tuning a local model: yes. In the same flow, an embedding recipe ran on a GPU container profile and embedded its whole input. It then failed only because its output dataset was not declared in the recipe I/O (a container rule, not a GPU issue).

The code env for that flow is a good example of what pulls a GPU image in. Its spec included `streamlit`, `matplotlib`, `seaborn`, `flask`, `deepl`, `dash`, `tqdm`, `pdfminer.six`, `scikit-learn`, `beautifulsoup4`, `PyMuPDF`, `bertopic`, `sentence-transformers`, `umap-learn`, `hdbscan`, `nltk`, `aiohttp`, `rapidfuzz`, `PyPDF2`, `adapters`, with `--extra-index-url https://download.pytorch.org/whl/cu126`. The resulting image carried torch 2.8.0+cu126 and CUDA libraries, and each container image build took about 16 minutes. The env was scoped to three container configs (`eks-default`, `eks-gpu`, `eks-gpu-big`), but a labelling recipe that only calls the LLM needs just `eks-default`.

A forecasting project's env, by contrast, was the standard "Visual ML and time series forecasting" CPU and GPU package sets plus a few extras, with no LLM or NLP packages at all, scoped to `eks-gpu` and `eks-highmemory`.

## Workshop on LLM workflows

For a short workshop on LLM workflows with a few dozen concurrent participants we sized a dedicated node group. The workflow described was: supply data to a model, build an inference endpoint, predict answers based on selected data. Because the model runs behind the LLM connection, not on the cluster, the conclusion was CPU and memory, not GPU: m5.4xlarge (16 vCPU, 64 GB) as the starting point, m5.8xlarge for heavier concurrency, and g4dn.2xlarge (T4) only if a model had to be loaded locally. The cost difference between those answers is roughly three to five times. Details and the sizing back-and-forth are in [workshop-infrastructure.md](workshop-infrastructure.md).

## Two "identical" Azure OpenAI connections

Users reported that creating an LLM with GPT-5.1 through the general Azure OpenAI connection failed ("not accepted", reference deployment name unavailable), while the same model worked through a second connection created for one team. The message from the platform side was that GPT-5.1 had "only rolled out to" that team so far, and only the deployment name `gpt-5.1-<team suffix>` was available.

First reading: the two connections point at different Azure OpenAI resources and the model was only deployed in the team's resource, so someone would have to ask for a deployment in the general resource.

But on inspection the two connections looked identical (same endpoint). If they point at the same Azure resource, the difference is not the resource. It is which deployment names are configured as available in each connection's settings in DSS. The team connection lists `gpt-5.1-<team suffix>` and the general one does not. Fix, if that holds: add the deployment name to the general connection's model list in Administration > Connections. Not confirmed. Whether access to that model should be opened to everyone is a policy question for whoever owns the Azure deployment.

When debugging LLM connection access, compare: endpoint/resource, auth, the list of deployments or models enabled in the connection, and the connection's usage permissions (which groups may use it).

## Copying an LLM flow to another project

The checklist we used when repeating the audit on a second project:

* code env attached to the recipes, its packages, and whether its container image is built (`dockerImageResources` not `INIT`)
* every dataset the code touches declared in recipe I/O
* no hard-coded project keys in the Python (grep for the source project key)
* the LLM connection is usable from the new project
* output token limits
* container exec enabled and the right container config chosen
* dataset name typos carried over from the source flow (one source dataset had a misspelled name that every downstream reference had to match).
