#!/bin/bash

# Usage: ./build_python.sh [py_version] [clang]
# Built python is linked to absolute path of libpython
# so it must rebuild after .cpython folder is moved
# uv venv --python --clear "./.cpython/bin/python3.XX"

set -e

PYTHON_VERSION="${1:-3.14.6}"
PREFIX="../../.cpython"

mkdir -p build
cd build
wget -nc "https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tgz"
tar xf "Python-${PYTHON_VERSION}.tgz"
cd "Python-${PYTHON_VERSION}"

export PREFIX=$(realpath -m "$PREFIX")
if [[ "$2" = "clang" ]]; then
    export CC=clang
    export CXX=clang++
    export LD=lld
fi

OLD_CFLAGS="${CFLAGS}"
OLD_LDFLAGS="${LDFLAGS}"

CFLAGS="${CFLAGS} \
    -fno-semantic-interposition \
    -fno-strict-overflow \
    -DNDEBUG \
    -g0 \
    -O3 \
    -D_Py_TIER2=3 \
    -march=x86-64 \
    -mtune=generic \
    -fvisibility=hidden"
LDFLAGS="${LDFLAGS} \
    -Wl,-O1 \
    -Wl,--sort-common \
    -Wl,--as-needed \
    -Wl,-z,pack-relative-relocs \
    -Wl,-s \
    -Wl,--exclude-libs,ALL \
    -Wl,-rpath,$PREFIX/lib \
    -LModules/_hacl"
[[ "$2" == "clang" ]] && CFLAGS="${CFLAGS} -flto=thin"
[[ "$2" == "clang" ]] && LDFLAGS="${LDFLAGS} -flto=thin -fuse-ld=lld"
export CFLAGS LDFLAGS

cat > ./Modules/Setup.local << EOF
*disabled*
_suggestions
_bz2
_lzma
_curses_panel
_tkinter
_gdbm
_dbm
xxsubtype
_xxtestfuzz
_testbuffer
_testinternalcapi
_testcapi
_testlimitedcapi
_testclinic
_testclinic_limited
_testimportmultiple
_testmultiphase
_testsinglephase
_ctypes_test
xxlimited
xxlimited_35
_multiprocessing
EOF

./configure ax_cv_c_float_words_bigendian=no \
    --prefix="$PREFIX" \
    --enable-shared \
    --enable-ipv6 \
    --enable-optimizations \
    --with-system-libmpdec \
    --without-ensurepip \
    --without-doc-strings \
    --without-remote-debug \
    --without-readline
make -j"$(nproc)"
make install

cd ../..
rm -rf build

CFLAGS="${OLD_CFLAGS}"
LDFLAGS="${OLD_LDFLAGS}"
export CFLAGS LDFLAGS
