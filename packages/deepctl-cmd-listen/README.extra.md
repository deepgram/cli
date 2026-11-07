## URL sources

`dg listen <URL>` checks a URL by format only (an http or https scheme and a host). Pass `--check-url` to check that the URL responds first: a HEAD request, then a 1-byte GET if needed. That check sends no Deepgram API key and keeps no cookies, but any `user:pass@` you put in the URL goes with it, as with any fetch.

Status lines and errors show the URL as `scheme://host/…/<last path segment>`, without user info, query string or fragment. In `-o json` and `-o yaml` output, `source_path` keeps the full path (automation needs it to identify the file) but drops the user info, query string and fragment. If your host puts a token in the URL path, treat `source_path` as sensitive. The request to Deepgram always uses the full URL.
