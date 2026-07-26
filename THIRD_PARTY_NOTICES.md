# Third-Party Notices

## BlenderMCP addon

The vendored `src/blendersessiond/vendor/addon.py` derives from
[`ahujasid/blender-mcp`](https://github.com/ahujasid/blender-mcp), copyright
2025 Siddharth Ahuja, licensed under the MIT License.

The vendored copy is modified: besides loopback binding, per-Session ports,
and managed startup, upstream's telemetry (consent handler, preference, and
UI) is deleted entirely — blendersessiond does not want the telemetry that
upstream ships, so managed Sessions never report usage, prompts, code, or
screenshots to the upstream backend. The complete delta is
`src/blendersessiond/vendor/addon.patch`, documented in `docs/compat.md`.

The MIT License text:

> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to
> deal in the Software without restriction, including without limitation the
> rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
> sell copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in
> all copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
> FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
> IN THE SOFTWARE.
