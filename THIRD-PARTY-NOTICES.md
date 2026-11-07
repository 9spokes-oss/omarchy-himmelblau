# Third-party notices

This project includes adaptations of components of [Omarchy](https://github.com/omacom/omarchy), including its lock screen and login theme. Omarchy is distributed under the following license:

Copyright (c) David Heinemeier Hansson

Permission is hereby granted, free of charge, to any person obtaining
a copy of this software and associated documentation files (the
"Software"), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so, subject to
the following conditions:

The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

[Himmelblau](https://github.com/himmelblau-idm/himmelblau) is a separate dependency, not included in this repository. Its own license and notices apply to its distribution.

## SDDM native integration

The patch under `packaging/sddm-himmelblau/` modifies [SDDM](https://github.com/sddm/sddm) v0.21.0, copyright its upstream contributors. It uses the release and Arch packaging revisions recorded in that directory's `provenance.json`; the conversation work also draws on [upstream pull request 2215](https://github.com/sddm/sddm/pull/2215). Original file copyright notices are retained in the patched source.

SDDM daemon, greeter and helper files are licensed under GPL-2.0-or-later. The `src/auth` library files retain LGPL-2.1-or-later as specified in their headers. These native modifications retain the licenses of their respective upstream files; the companion project's MIT license does not replace them. License texts are included in [LICENSES/SDDM-GPL-2.0.txt](LICENSES/SDDM-GPL-2.0.txt) and [LICENSES/SDDM-LGPL-2.1.txt](LICENSES/SDDM-LGPL-2.1.txt). Bundled upstream themes and assets retain their own notices and licenses in the source and package.

The native patch embeds `src/recovery-account.h` as SDDM's
`src/common/RecoveryAccount.h`. That shared header is original companion code under
MIT; the enclosing SDDM sources retain their existing GPL/LGPL licenses. Build checks
require the embedded header to match the companion header byte for byte.
