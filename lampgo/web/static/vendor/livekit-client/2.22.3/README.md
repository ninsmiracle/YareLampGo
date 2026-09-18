# LiveKit browser client 2.22.3

Vendored from the official `livekit-client` npm package so voice calls do not
need a browser-side CDN download. The ESM bundle and LICENSE are unmodified.
The optional development source map is not included.

- Upstream: https://github.com/livekit/client-sdk-js
- Package: https://registry.npmjs.org/livekit-client/-/livekit-client-2.22.3.tgz
- License: Apache-2.0; see LICENSE and upstream notices retained in the bundle.
- npm archive integrity: `sha512-jw9zBKXY5Gtr5MZ7vEON3QhMNccuDvYHck1PFSyG1aaateQPqgKZFBMgZkFZaXHIf9RV4MDW5xpTK2b/+qbwOg==`
- Bundle SHA-256: `23e6b0966c20ccaba8d39343035fcc49e64aa46c93e772ca0a3f1b5a6d30b573`

To update, download an explicit npm version, verify the archive integrity against
its npm metadata, and copy its ESM bundle and license to a new version directory.
Update the import in app.js and run tests/test_livekit_client.cjs plus the browser
call lifecycle tests. Verify that the wheel includes the vendor files and that
the browser can load the client when third-party asset requests are blocked.
