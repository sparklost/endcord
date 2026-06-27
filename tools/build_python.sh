#!/bin/bash

# Usage: ./build_python.sh [py_version] [clang] [curses] [curses_version]
# Built python is linked to absolute path of libpython
# so it must be rebuilt after .cpython folder is moved
# uv venv --python --clear "./.cpython/bin/python3.XX"

set -e

if [[ "$2" = "clang" ]]; then
    export CC=clang
    export CXX=clang++
    export LD=lld
fi

OLD_CFLAGS="${CFLAGS}"
OLD_LDFLAGS="${LDFLAGS}"

mkdir -p build
cd build



### CURSES ###

if [[ "$3" = "curses" ]]; then
    if [ -n "$4" ]; then
        CURSES_TAG="$4"
    else
        CURSES_TAG=$(git ls-remote --tags --refs https://github.com/ThomasDickey/ncurses-snapshots.git | awk -F/ '{print $3}' | sort -V | tail -n1)
    fi
    echo "Building curses ${CURSES_TAG}"
    wget -nc "https://github.com/ThomasDickey/ncurses-snapshots/archive/refs/tags/${CURSES_TAG}.tar.gz" -O "ncurses-${CURSES_TAG}.tar.gz" || true
    tar xf "ncurses-${CURSES_TAG}.tar.gz"
    cd ncurses-snapshots-*
    # dont link agains test libraries
    find . -type f -exec sed -i 's/ @SHLIB_LIST@//g' {} +

    PREFIX="../../.cpython"
    export PREFIX=$(realpath -m "$PREFIX")
    mkdir -p "$PREFIX"
    CFLAGS="${CFLAGS} \
        -fno-semantic-interposition \
        -fno-strict-overflow \
        -DNDEBUG \
        -g0 \
        -O3 \
        -mtune=generic \
        -fvisibility=hidden \
        -ffat-lto-objects"
    LDFLAGS="${LDFLAGS} \
        -Wl,-O1 \
        -Wl,--sort-common \
        -Wl,--as-needed \
        -Wl,-z,pack-relative-relocs \
        -Wl,-s \
        -Wl,--exclude-libs,ALL \
        -ffat-lto-objects"
    export CFLAGS LDFLAGS

    ./configure \
        --prefix="$PREFIX" \
        --enable-pc-files \
        --enable-widec \
        --with-cxx-shared \
        --with-pkg-config-libdir="$PREFIX/lib/pkgconfig" \
        --with-shared \
        --with-versioned-syms \
        --with-xterm-kbs=del \
        --without-ada \
        --without-debug
    make -j"$(nproc)"
    make install
    cd ..
    CFLAGS=$OLD_CFLAGS
    LDFLAGS=$OLD_LDFLAGS
fi



### PYTHON ###

PYTHON_VERSION="${1:-3.14.6}"
echo "Building Python ${PYTHON_VERSION}"
wget -nc "https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tgz" || true
tar xf "Python-${PYTHON_VERSION}.tgz"
cd "Python-${PYTHON_VERSION}"

PREFIX="../../.cpython"
export PREFIX=$(realpath -m "$PREFIX")
mkdir -p "$PREFIX"
if [[ "$3" = "curses" ]]; then
    CFLAGS="${CFLAGS} -I$PREFIX/include/ncursesw -I$PREFIX/include"
    LDFLAG="${LDFLAGS} -L$PREFIX/lib"
    export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig:$PKG_CONFIG_PATH"
fi
CFLAGS="${CFLAGS} \
    -fno-semantic-interposition \
    -fno-strict-overflow \
    -DNDEBUG \
    -g0 \
    -O3 \
    -D_Py_TIER2=3 \
    -mtune=generic \
    -fvisibility=hidden \
    -I/usr/include"
LDFLAGS="${LDFLAGS} \
    -Wl,-O1 \
    -Wl,--sort-common \
    -Wl,--as-needed \
    -Wl,-z,pack-relative-relocs \
    -Wl,-s \
    -Wl,--exclude-libs,ALL \
    -Wl,-rpath,$PREFIX/lib \
    -L/usr/lib \
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

CFLAGS=$OLD_CFLAGS
LDFLAGS=$OLD_LDFLAGS
export CFLAGS LDFLAGS
