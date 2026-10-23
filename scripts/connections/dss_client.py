"""Get a DSS API client, inside DSS or from outside it.

Inside a DSS notebook or recipe, the internal client is used and no key is
needed. Outside, set:

    DSS_HOST       https://dss.example.com
    DSS_API_KEY    a personal or global API key (admin rights for most scripts)
    DSS_VERIFY_TLS set to "false" only for test instances with self-signed certs
"""

import os


def get_client():
    try:
        import dataiku  # only importable inside DSS

        return dataiku.api_client()
    except ImportError:
        pass

    import dataikuapi

    try:
        host = os.environ["DSS_HOST"]
        api_key = os.environ["DSS_API_KEY"]
    except KeyError as missing:
        raise SystemExit(f"Set {missing.args[0]} (see dss_client.py)") from None

    verify = os.environ.get("DSS_VERIFY_TLS", "true").lower() != "false"
    return dataikuapi.DSSClient(host, api_key, no_check_certificate=not verify)
