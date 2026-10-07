#!/usr/bin/env bash
#
# Run on the host by initializeCommand, before the image is built. Copies the
# CA certificates an administrator added to the host's trust store, such as the
# root CA of a TLS-intercepting corporate proxy, into certs/ so the Dockerfile
# installs them. Hosts without such certificates copy nothing.
set -euo pipefail

dest="$(cd "$(dirname "$0")" && pwd)/certs"
mkdir -p "${dest}"
# Only files this script wrote; certificates placed in certs/ by hand are kept.
rm -f "${dest}"/host-*.crt

case "$(uname -s)" in
Linux)
    # Debian/Ubuntu (including WSL) and Fedora/RHEL anchor directories.
    for dir in /usr/local/share/ca-certificates /etc/pki/ca-trust/source/anchors; do
        [[ -d "${dir}" ]] || continue
        while IFS= read -r -d '' cert; do
            name="$(basename "${cert}")"
            cp "${cert}" "${dest}/host-${name%.*}.crt"
        done < <(find "${dir}" -type f \( -name '*.crt' -o -name '*.pem' \) -print0)
    done
    ;;
Darwin)
    # update-ca-certificates expects one certificate per file.
    security find-certificate -a -p /Library/Keychains/System.keychain |
        awk -v dir="${dest}" '/BEGIN CERTIFICATE/ { n++ } { print > (dir "/host-macos-" n ".crt") }'
    ;;
esac

count="$(find "${dest}" -name 'host-*.crt' | wc -l)"
echo "collect-host-certs: ${count} host CA certificate(s) in ${dest}"
