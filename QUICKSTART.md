# Quick Start Guide - Debian Repository Setup

This guide provides a quick walkthrough for setting up the automated Debian package repository.

## Prerequisites

- A fork of this repository
- GitHub Actions enabled
- GPG installed on your local machine

## One-Time Setup

### 1. Generate and Configure GPG Keys

Run the included helper script:

```bash
./setup-gpg-keys.sh
```

This script will:
- Help you generate a GPG key (or use an existing one)
- Export the private key for GitHub Actions
- Export the public key for users
- Provide step-by-step instructions

**Alternative manual method:**

```bash
# Generate key
gpg --full-generate-key

# List keys and note the KEY_ID
gpg --list-secret-keys --keyid-format LONG

# Export for GitHub
gpg --export-secret-keys --armor <KEY_ID> > private.key
gpg --export --armor <KEY_ID> > public.key
```

### 2. Add GitHub Secrets

Go to: `Settings → Secrets and variables → Actions`

Add two secrets:
- **Name:** `GPG_PRIVATE_KEY`
  **Value:** Full content of `private.key`

- **Name:** `GPG_PASSPHRASE`
  **Value:** Your GPG key passphrase

### 3. Enable GitHub Pages

Go to: `Settings → Pages`

Configure:
- **Source:** Deploy from a branch
- **Branch:** gh-pages
- **Folder:** / (root)

Click **Save**

### 4. Trigger First Build

Go to: `Actions → Build and Publish Debian Repository → Run workflow`

Click: **Run workflow** on the main branch

Wait 5-10 minutes for completion.

### 5. Upload Public Key to gh-pages

After the first successful workflow run:

```bash
# Clone your repository if you haven't
git clone https://github.com/YOUR_USERNAME/endcord.git
cd endcord

# Switch to gh-pages branch
git fetch origin gh-pages
git checkout gh-pages

# Copy the public key
cp /path/to/gpg-exports/public.key .

# Commit and push
git add public.key
git commit -m "Add GPG public key for package verification"
git push origin gh-pages
```

### 6. Verify GitHub Pages Deployment

After a few minutes, check:
```
https://YOUR_USERNAME.github.io/endcord/
```

You should see the repository structure with `pool/`, `dists/`, etc.

## User Installation Instructions

Share these instructions with users who want to install from your repository:

### For Debian 13 (Trixie) / LMDE 7 Users:

```bash
# 1. Add GPG key
wget -qO - https://YOUR_USERNAME.github.io/endcord/public.key | sudo gpg --dearmor -o /etc/apt/keyrings/endcord-archive-keyring.gpg

# 2. Add repository
echo "deb [signed-by=/etc/apt/keyrings/endcord-archive-keyring.gpg arch=amd64] https://YOUR_USERNAME.github.io/endcord/ stable main" | sudo tee /etc/apt/sources.list.d/endcord.list

# 3. Install
sudo apt update
sudo apt install endcord
```

**Replace `YOUR_USERNAME` with your actual GitHub username!**

## Ongoing Maintenance

### Automatic Nightly Builds

The workflow runs automatically every day at 2:00 AM UTC. It will:
1. Pull the latest code from upstream `sparklost/endcord`
2. Build a new package with version `X.Y.Z-nightYYYYMMDD`
3. Update the repository on gh-pages
4. Users will get updates via `sudo apt update && sudo apt upgrade`

### Manual Builds

Trigger manually anytime via:
`Actions → Build and Publish Debian Repository → Run workflow`

### Monitoring Builds

Check build status:
`Actions → Build and Publish Debian Repository`

View logs for any failures and troubleshoot as needed.

### Customizing Schedule

Edit `.github/workflows/debian-repo.yml`:

```yaml
on:
  schedule:
    - cron: '0 2 * * *'  # Change to your preferred time
```

Examples:
- `'0 0 * * *'` - Midnight UTC daily
- `'0 */6 * * *'` - Every 6 hours
- `'0 2 * * 1'` - 2 AM UTC every Monday

## Troubleshooting

### Build Fails: "GPG signing failed"

**Solution:** Check that GitHub secrets are correctly configured:
- `GPG_PRIVATE_KEY` should contain the full armor-encoded private key
- `GPG_PASSPHRASE` should be the exact passphrase

### Build Fails: "PyInstaller error"

**Solution:** This is usually due to dependency issues in upstream. Check the Actions log for details. You may need to:
- Wait for upstream to fix dependencies
- Manually patch `pyproject.toml` in the workflow

### Repository Not Found (404)

**Solution:**
1. Ensure GitHub Pages is enabled
2. Check that gh-pages branch exists
3. Wait 5-10 minutes after the first workflow run

### APT Update Fails: "NO_PUBKEY"

**Solution:**
1. Verify `public.key` is in the root of gh-pages branch
2. Re-run the wget command to download the key
3. Verify the key fingerprint matches

### Packages Not Updating

**Solution:**
1. Check Actions tab to ensure workflow is running
2. Verify cron schedule is correct
3. Check if upstream has new commits

## Advanced Configuration

### Switch to Nuitka for Optimized Builds

If you have GitHub paid runners or want to wait longer:

Edit `.github/workflows/debian-repo.yml`, change:
```yaml
uv run build.py --onefile
```
to:
```yaml
uv run build.py --nuitka --onefile
```

**Note:** This will increase build time from ~5-10 minutes to ~30-60 minutes.

### Add Multiple Architectures

Currently only `amd64` is supported. To add `arm64`:

1. Modify the workflow to build on multiple runners
2. Add arm64 to the `Architectures:` field in Release file
3. Create `dists/stable/main/binary-arm64/` directory structure

### Private Repository

To host packages in a private repository:

1. Generate a Personal Access Token with `repo` scope
2. Users must authenticate: `apt-key adv --keyserver...` with authentication
3. Or use GitHub Packages instead

## Security Notes

- **Never commit** `private.key` to the repository
- **Never share** your GPG passphrase
- Keep GitHub secrets secure
- Regularly rotate GPG keys (every 1-2 years)
- Monitor Actions logs for unusual activity

## Getting Help

- **Packaging Issues:** Open an issue in this repository
- **Endcord Issues:** Report to [sparklost/endcord](https://github.com/sparklost/endcord)
- **GitHub Actions Issues:** Check [GitHub Actions documentation](https://docs.github.com/actions)

## Reference Links

- [DEBIAN_REPOSITORY.md](DEBIAN_REPOSITORY.md) - Full documentation
- [GitHub Actions Documentation](https://docs.github.com/actions)
- [Debian Repository Format](https://wiki.debian.org/DebianRepository/Format)
- [APT Package Management](https://wiki.debian.org/Apt)

---

**Quick Checklist:**

- [ ] Run `./setup-gpg-keys.sh` and generate keys
- [ ] Add `GPG_PRIVATE_KEY` secret to GitHub
- [ ] Add `GPG_PASSPHRASE` secret to GitHub
- [ ] Enable GitHub Pages (gh-pages branch, root folder)
- [ ] Manually trigger first workflow run
- [ ] Wait for completion (~5-10 min)
- [ ] Upload `public.key` to gh-pages branch
- [ ] Verify repository is accessible at `https://YOUR_USERNAME.github.io/endcord/`
- [ ] Test installation on Debian 13 or LMDE 7
- [ ] Share installation instructions with users

Done! Your automated Debian repository is now live. 🎉
