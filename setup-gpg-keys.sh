#!/bin/bash
# Setup script for Debian repository GPG keys
# This script helps generate and export GPG keys for package signing

set -e

echo "=================================="
echo "Endcord Debian Repository Setup"
echo "=================================="
echo ""

# Check if GPG is installed
if ! command -v gpg &> /dev/null; then
    echo "Error: GPG is not installed. Please install it first:"
    echo "  Debian/Ubuntu: sudo apt install gnupg"
    echo "  macOS: brew install gnupg"
    exit 1
fi

# Verify that GPG is functional
if ! gpg --version > /dev/null 2>&1; then
    echo "Error: GPG appears to be installed but is not working correctly."
    echo "Please verify your GPG installation (e.g., reinstall gnupg) and try again."
    exit 1
fi
echo "This script will help you set up GPG keys for signing Debian packages."
echo ""
echo "Step 1: Generate a GPG key (if you don't have one)"
echo "======================================================="
echo ""
read -p "Do you want to generate a new GPG key? (y/n): " generate_key

if [[ "$generate_key" == "y" || "$generate_key" == "Y" ]]; then
    echo ""
    echo "Generating GPG key..."
    echo "Please follow the prompts:"
    echo "  - Select (1) RSA and RSA"
    echo "  - Use 4096 bits"
    echo "  - Enter your name (e.g., 'Endcord Builder')"
    echo "  - Enter your email"
    echo "  - Set a strong passphrase"
    echo ""
    gpg --full-generate-key
    echo ""
    echo "GPG key generated successfully!"
fi

echo ""
echo "Step 2: List your GPG keys"
echo "======================================================="
echo ""
gpg --list-secret-keys --keyid-format LONG

echo ""
echo "Step 3: Export keys for GitHub"
echo "======================================================="
echo ""
read -p "Enter your GPG key ID (the long hex string after 'rsa4096/'): " key_id

if [[ -z "$key_id" ]]; then
    echo "Error: Key ID cannot be empty"
    exit 1
fi

# Create a directory for exports
mkdir -p gpg-exports
cd gpg-exports

echo ""
echo "Exporting private key for GitHub Actions..."
gpg --export-secret-keys --armor "$key_id" > private.key
echo "✓ Private key saved to: gpg-exports/private.key"

echo ""
echo "Exporting public key for users..."
gpg --export --armor "$key_id" > public.key
echo "✓ Public key saved to: gpg-exports/public.key"

echo ""
echo "Step 4: Configuration Instructions"
echo "======================================================="
echo ""
echo "1. Add GitHub Secrets:"
echo "   - Go to: Settings → Secrets and variables → Actions"
echo "   - Add secret 'GPG_PRIVATE_KEY' with content from: gpg-exports/private.key"
echo "   - Add secret 'GPG_PASSPHRASE' with your GPG passphrase"
echo ""
echo "2. After the first workflow run, upload public.key to gh-pages branch:"
echo "   git checkout gh-pages"
echo "   cp gpg-exports/public.key ."
echo "   git add public.key"
echo "   git commit -m 'Add GPG public key'"
echo "   git push"
echo ""
echo "3. Enable GitHub Pages:"
echo "   - Go to: Settings → Pages"
echo "   - Source: Deploy from a branch"
echo "   - Branch: gh-pages, folder: / (root)"
echo ""
echo "4. Manual workflow trigger (optional):"
echo "   - Go to: Actions → Build and Publish Debian Repository → Run workflow"
echo ""
echo "=================================="
echo "Setup complete!"
echo "=================================="
echo ""
echo "IMPORTANT: Keep your private key secure!"
echo "The private key has been saved to: gpg-exports/private.key"
echo "After uploading to GitHub Secrets, consider deleting this file."
echo ""
echo "Your GPG key ID is: $key_id"
echo "Your public key fingerprint is:"
gpg --fingerprint "$key_id"
echo ""
echo "Keys are stored in: $(pwd)/gpg-exports/"
echo "Remember to delete private.key after uploading to GitHub Secrets!"

# Return to original directory
cd ..
