# Endcord Debian Repository

This repository provides automated nightly builds of Endcord as Debian packages (`.deb`) for Debian 13 (Trixie) and LMDE 7 (Linux Mint Debian Edition).

## What is this?

This fork of Endcord includes a sophisticated CI/CD pipeline that:
1. **Syncs daily** with the upstream [sparklost/endcord](https://github.com/sparklost/endcord) repository at 2:00 AM UTC
2. **Builds** a single-file executable using PyInstaller
3. **Packages** it into a signed `.deb` file with proper Debian metadata
4. **Publishes** to a GitHub Pages-hosted APT repository
5. **Enables** automatic updates via `sudo apt update && sudo apt upgrade`

## Installation Instructions

### Step 1: Add the Repository GPG Key

The repository packages are signed with a GPG key to ensure authenticity. Add the public key to your system:

```bash
wget -qO - https://oichkatzelesfrettschen.github.io/endcord/public.key | sudo gpg --dearmor -o /etc/apt/keyrings/endcord-archive-keyring.gpg
```

**Note:** The `public.key` file needs to be manually uploaded to the `gh-pages` branch after the first workflow run. See [Setup Instructions](#setup-instructions-for-repository-maintainer) below.

### Step 2: Add the Repository to APT Sources

Create a new APT source list file:

```bash
echo "deb [signed-by=/etc/apt/keyrings/endcord-archive-keyring.gpg arch=amd64] https://oichkatzelesfrettschen.github.io/endcord/ stable main" | sudo tee /etc/apt/sources.list.d/endcord.list
```

### Step 3: Update Package Lists and Install

```bash
sudo apt update
sudo apt install endcord
```

### Step 4: Run Endcord

After installation, you can run Endcord from anywhere:

```bash
endcord
```

The application will also appear in your application menu as "Endcord".

## Automatic Updates

Once the repository is configured, Endcord will automatically update when you run:

```bash
sudo apt update
sudo apt upgrade
```

Nightly builds are versioned as `<base_version>-nightly<YYYYMMDD>` (e.g., `1.1.7-nightly20260106`).

## Uninstallation

To remove Endcord:

```bash
sudo apt remove endcord
```

To also remove the repository configuration:

```bash
sudo rm /etc/apt/sources.list.d/endcord.list
sudo rm /etc/apt/keyrings/endcord-archive-keyring.gpg
sudo apt update
```

## Architecture

### Build Pipeline

The GitHub Action (`.github/workflows/debian-repo.yml`) performs:

1. **Upstream Sync**: Fetches latest code from `sparklost/endcord`
2. **Version Tagging**: Creates nightly versions based on date
3. **Binary Build**: Uses PyInstaller for fast builds (Nuitka optional but slow on free runners)
4. **Debian Packaging**: Creates proper `.deb` structure with:
   - Binary in `/usr/bin/endcord`
   - Desktop entry in `/usr/share/applications/endcord.desktop`
   - Control file with metadata
5. **Repository Generation**: Creates APT repository metadata:
   - `pool/main/` - Contains all `.deb` files
   - `dists/stable/main/binary-amd64/Packages` - Package index
   - `dists/stable/Release` - Repository metadata
   - `dists/stable/InRelease` - Signed repository metadata
6. **Deployment**: Pushes to `gh-pages` branch

### Repository Structure (gh-pages branch)

```
/
├── pool/
│   └── main/
│       └── endcord_*.deb
├── dists/
│   └── stable/
│       ├── Release
│       ├── Release.gpg
│       ├── InRelease
│       └── main/
│           └── binary-amd64/
│               ├── Packages
│               └── Packages.gz
└── public.key
```

## Setup Instructions (For Repository Maintainer)

### Prerequisites

You need a GPG key to sign packages. Generate one if you don't have it:

```bash
# Generate a new GPG key
gpg --full-generate-key
# Select (1) RSA and RSA, 4096 bits
# Enter your name (e.g., "Endcord Builder") and email
# Set a passphrase

# List keys to find the key ID
gpg --list-secret-keys --keyid-format LONG

# Export private key for GitHub Actions
gpg --export-secret-keys --armor <YOUR_KEY_ID> > private.key

# Export public key for users
gpg --export --armor <YOUR_KEY_ID> > public.key
```

### Configure GitHub Secrets

Go to your repository **Settings → Secrets and variables → Actions** and add:

1. `GPG_PRIVATE_KEY`: Content of `private.key`
2. `GPG_PASSPHRASE`: The passphrase you set during key generation

### Enable GitHub Pages

1. Go to **Settings → Pages**
2. Set source to **Deploy from a branch**
3. Select **gh-pages** branch and **/ (root)** folder
4. Save

### Upload Public Key

After the first successful workflow run:

1. Switch to the `gh-pages` branch
2. Add your `public.key` file to the root
3. Commit and push:
   ```bash
   git checkout gh-pages
   cp /path/to/public.key .
   git add public.key
   git commit -m "Add GPG public key"
   git push
   ```

### Manual Trigger

You can manually trigger the workflow:

1. Go to **Actions** tab
2. Select **Build and Publish Debian Repository**
3. Click **Run workflow**

## Scheduled Builds

The workflow runs automatically every day at 2:00 AM UTC. You can adjust the schedule by editing the cron expression in `.github/workflows/debian-repo.yml`:

```yaml
on:
  schedule:
    - cron: '0 2 * * *'  # Change this to your preferred time
```

## Troubleshooting

### Package verification failed

If you get GPG errors, ensure:
1. The public key is correctly uploaded to the `gh-pages` branch
2. You're using the correct URL in the `wget` command
3. The keyring file was created successfully in `/etc/apt/keyrings/`

### Build failures

Check the **Actions** tab in GitHub for detailed logs. Common issues:
- PyInstaller build failures: May need to adjust dependencies in `pyproject.toml`
- GPG signing failures: Check that secrets are correctly configured
- Permission issues: Ensure the workflow has `contents: write` permission

### Repository not found

Ensure GitHub Pages is enabled and the `gh-pages` branch exists. After the first workflow run, it may take a few minutes for GitHub Pages to deploy.

## Technical Details

### Why PyInstaller instead of Nuitka?

While Nuitka produces more optimized binaries, it's significantly slower (30-60 minutes vs 5-10 minutes). For nightly builds on free GitHub runners, PyInstaller provides a better balance. If you have access to paid runners with more resources, you can switch to Nuitka by changing the build step:

```yaml
- name: Build Binary (Nuitka)
  working-directory: ./endcord-upstream
  run: |
    uv sync --group build
    uv run build.py --nuitka --onefile
```

### Why Ubuntu 24.04?

Ubuntu 24.04 uses glibc 2.39, which is compatible with Debian Trixie (glibc 2.40). This ensures binary compatibility.

### Package Signing

All packages are signed with GPG to ensure authenticity and integrity. The APT repository metadata (`Release`, `InRelease`) is also signed, which APT verifies before accepting updates.

## License

This repository follows the same license as the upstream Endcord project. See [LICENSE](LICENSE) for details.

## Contributing

For issues with the packaging or repository setup, please open an issue in this repository. For issues with Endcord itself, please report them to the [upstream repository](https://github.com/sparklost/endcord).

## Credits

- **Upstream Endcord**: [sparklost/endcord](https://github.com/sparklost/endcord)
- **CI/CD Architecture**: Designed for automated Debian package distribution via GitHub Pages
