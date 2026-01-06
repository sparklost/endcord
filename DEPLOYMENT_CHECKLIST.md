# Deployment Checklist - Endcord Debian Repository

**⚠️ Important:** This is an unofficial community repack. Complete these steps to activate the automated Debian package repository.

## Prerequisites

- [ ] You have forked this repository
- [ ] You have local access to a machine with GPG installed
- [ ] You have admin access to your GitHub repository

---

## Step 1: Generate GPG Keys (Local Machine)

**Time required:** ~5 minutes

Run the setup script on your local machine:

```bash
# Clone your repository if you haven't already
git clone https://github.com/Oichkatzelesfrettschen/endcord.git
cd endcord

# Make the script executable (if needed)
chmod +x setup-gpg-keys.sh

# Run the setup script
./setup-gpg-keys.sh
```

The script will:
1. Check if GPG is installed
2. Help you generate a new GPG key (or use existing)
3. Export keys to `gpg-exports/` directory
4. Display your key ID and fingerprint

**Important:** Keep your passphrase secure! You'll need it for GitHub secrets.

---

## Step 2: Add GitHub Secrets

**Time required:** ~2 minutes

1. Go to your repository on GitHub: `https://github.com/Oichkatzelesfrettschen/endcord`
2. Click **Settings** → **Secrets and variables** → **Actions**
3. Click **New repository secret**

Add these two secrets:

### Secret 1: `GPG_PRIVATE_KEY`
- **Name:** `GPG_PRIVATE_KEY`
- **Value:** Copy the entire contents of `gpg-exports/private.key`
  ```bash
  cat gpg-exports/private.key
  ```
- Click **Add secret**

### Secret 2: `GPG_PASSPHRASE`
- **Name:** `GPG_PASSPHRASE`
- **Value:** The passphrase you set when creating the GPG key
- Click **Add secret**

**Security note:** After adding secrets, you can delete the local `gpg-exports/private.key` file.

---

## Step 3: Enable GitHub Pages

**Time required:** ~1 minute

1. Go to **Settings** → **Pages**
2. Under **Source**, select:
   - **Source:** Deploy from a branch
   - **Branch:** `gh-pages` (will be created automatically by first workflow run)
   - **Folder:** `/ (root)`
3. Click **Save**

**Note:** The gh-pages branch will be created by the first workflow run. You'll see a 404 error until then - this is normal.

---

## Step 4: Trigger First Workflow Run

**Time required:** ~10 minutes (for workflow to complete)

### Option A: Via GitHub Web UI
1. Go to **Actions** tab
2. Click **Build and Publish Debian Repository** workflow
3. Click **Run workflow** dropdown
4. Select branch: `copilot/create-github-pages-ppa` (or `main` after merging)
5. Click **Run workflow** button

### Option B: Via GitHub CLI (if installed locally)
```bash
gh workflow run debian-repo.yml
```

### What happens during the workflow:
- Syncs latest code from upstream sparklost/endcord
- Builds binary with PyInstaller (~5-10 minutes)
- Creates .deb package
- Signs package with your GPG key
- Generates APT repository metadata
- Deploys to gh-pages branch

**Monitor progress:** Click on the workflow run to see live logs.

---

## Step 5: Upload Public Key to gh-pages

**Time required:** ~2 minutes

After the first workflow completes successfully:

```bash
# Switch to gh-pages branch
git fetch origin gh-pages
git checkout gh-pages

# Copy your public key
cp /path/to/gpg-exports/public.key .

# Or if you're in the main branch directory:
# git checkout main
# cp gpg-exports/public.key /tmp/public.key
# git checkout gh-pages
# cp /tmp/public.key .

# Commit and push
git add public.key
git commit -m "Add GPG public key for package verification"
git push origin gh-pages

# Return to your working branch
git checkout copilot/create-github-pages-ppa
```

---

## Step 6: Verify Deployment

**Time required:** ~3 minutes

### Check GitHub Pages
1. Wait 2-3 minutes for GitHub Pages to deploy
2. Visit: `https://oichkatzelesfrettschen.github.io/endcord/`
3. You should see directory listing with `pool/`, `dists/`, and `public.key`

### Check Repository Structure
```bash
git checkout gh-pages
tree -L 3  # or: find . -type f | head -20
```

Expected structure:
```
.
├── pool/
│   └── main/
│       └── endcord_1.1.7-nightly20260106_amd64.deb
├── dists/
│   └── stable/
│       ├── Release
│       ├── InRelease
│       ├── Release.gpg
│       └── main/
│           └── binary-amd64/
│               ├── Packages
│               └── Packages.gz
└── public.key
```

---

## Step 7: Test Installation (Optional but Recommended)

**Time required:** ~5 minutes

On a Debian 13 / LMDE 7 system (or VM):

```bash
# Add the repository key
wget -qO - https://oichkatzelesfrettschen.github.io/endcord/public.key | \
  sudo gpg --dearmor -o /etc/apt/keyrings/endcord-archive-keyring.gpg

# Add the repository
echo "deb [signed-by=/etc/apt/keyrings/endcord-archive-keyring.gpg arch=amd64] https://oichkatzelesfrettschen.github.io/endcord/ stable main" | \
  sudo tee /etc/apt/sources.list.d/endcord.list

# Update and install
sudo apt update
sudo apt install endcord

# Test
endcord --version
```

---

## Step 8: Share Installation Instructions

**Time required:** ~5 minutes

Create a new issue or update your README with user-facing installation instructions:

### For Users

```markdown
## Installation from Community APT Repository

**⚠️ Note:** This is an unofficial community repack for easier Debian installation.

### Debian 13 (Trixie) / LMDE 7

1. Add the repository key:
   ```bash
   wget -qO - https://oichkatzelesfrettschen.github.io/endcord/public.key | \
     sudo gpg --dearmor -o /etc/apt/keyrings/endcord-archive-keyring.gpg
   ```

2. Add the repository:
   ```bash
   echo "deb [signed-by=/etc/apt/keyrings/endcord-archive-keyring.gpg arch=amd64] https://oichkatzelesfrettschen.github.io/endcord/ stable main" | \
     sudo tee /etc/apt/sources.list.d/endcord.list
   ```

3. Install:
   ```bash
   sudo apt update
   sudo apt install endcord
   ```

### Automatic Updates

Updates are installed automatically with:
```bash
sudo apt upgrade
```

New nightly builds are published daily at 2:00 AM UTC.
```

---

## Maintenance

### Automatic Nightly Builds
- The workflow runs automatically every day at 2:00 AM UTC
- Syncs latest code from upstream sparklost/endcord
- Builds new package with version `X.Y.Z-nightYYYYMMDD`
- Updates repository automatically

### Manual Trigger
Re-run the workflow anytime via Actions tab → Run workflow

### Check Workflow Status
Monitor at: `https://github.com/Oichkatzelesfrettschen/endcord/actions`

---

## Troubleshooting

### Issue: Workflow fails with "GPG signing failed"
**Solution:** Check that both secrets are set correctly:
- `GPG_PRIVATE_KEY` contains the full armor-encoded private key
- `GPG_PASSPHRASE` matches the key's passphrase

### Issue: GitHub Pages shows 404
**Solutions:**
1. Wait 5-10 minutes after first workflow run
2. Check Settings → Pages is enabled with gh-pages branch
3. Ensure workflow completed successfully

### Issue: APT can't verify repository
**Solutions:**
1. Ensure `public.key` is uploaded to gh-pages branch
2. Verify URL: `https://oichkatzelesfrettschen.github.io/endcord/public.key`
3. Check key fingerprint matches

For more help, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md)

---

## Completion Checklist

- [ ] GPG keys generated
- [ ] GitHub secrets added (GPG_PRIVATE_KEY, GPG_PASSPHRASE)
- [ ] GitHub Pages enabled
- [ ] First workflow run completed successfully
- [ ] Public key uploaded to gh-pages
- [ ] Repository verified at GitHub Pages URL
- [ ] Test installation completed (optional)
- [ ] Installation instructions shared with users

**Status:** 🎉 Once all items are checked, your automated Debian repository is live!

---

## Next Steps After Completion

1. **Merge this PR** to main branch
2. **Update workflow schedule** if you want different build times
3. **Monitor workflow runs** for any issues
4. **Share repository** with Debian/LMDE users
5. **Consider adding** to awesome lists or communities

For questions or issues, open a GitHub issue or refer to the documentation:
- [DEBIAN_REPOSITORY.md](DEBIAN_REPOSITORY.md) - Full documentation
- [QUICKSTART.md](QUICKSTART.md) - Quick reference
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) - Common issues
