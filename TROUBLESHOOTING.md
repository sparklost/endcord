# CI/CD Troubleshooting Guide

This guide helps diagnose and fix common issues with the Debian repository CI/CD pipeline.

## Checking Build Status

### View Recent Workflow Runs

1. Go to your repository on GitHub
2. Click the **Actions** tab
3. Select **Build and Publish Debian Repository**
4. View the list of recent runs with their status (✓ success, ✗ failed)

### View Detailed Logs

1. Click on a specific workflow run
2. Click on the **build-and-publish** job
3. Expand each step to view detailed logs
4. Look for red ✗ marks indicating failures

## Common Issues and Solutions

### Issue 1: GPG Signing Fails

**Symptoms:**
```
Error: gpg: signing failed: No secret key
```

**Causes:**
- `GPG_PRIVATE_KEY` secret not configured
- `GPG_PASSPHRASE` secret incorrect
- Private key format invalid

**Solutions:**

1. **Verify secrets are set:**
   - Go to: Settings → Secrets and variables → Actions
   - Confirm both `GPG_PRIVATE_KEY` and `GPG_PASSPHRASE` exist

2. **Re-export the private key:**
   ```bash
   gpg --list-secret-keys --keyid-format LONG
   gpg --export-secret-keys --armor <KEY_ID> > private.key
   ```
   
3. **Update the secret:**
   - Copy the **entire content** of `private.key` including:
     - `-----BEGIN PGP PRIVATE KEY BLOCK-----`
     - All the encoded content
     - `-----END PGP PRIVATE KEY BLOCK-----`
   - Paste into GitHub secret (no extra spaces or newlines)

4. **Verify passphrase:**
   - Test locally: `echo "test" | gpg --clearsign --default-key <KEY_ID>`
   - If it fails, your passphrase is wrong

### Issue 2: PyInstaller Build Fails

**Symptoms:**
```
ModuleNotFoundError: No module named 'xxx'
ERROR: Unable to import 'xxx'
```

**Causes:**
- Missing dependencies in upstream code
- Incompatible Python version
- PyInstaller version mismatch

**Solutions:**

1. **Check upstream repository:**
   - Visit: https://github.com/sparklost/endcord
   - Check recent commits for dependency changes
   - Look for open issues about build failures

2. **Wait for upstream fix:**
   - The issue may be temporary
   - Check if next day's build succeeds

3. **Manually patch dependencies (advanced):**
   - Fork the workflow
   - Add a step to patch `pyproject.toml` before building:
   ```yaml
   - name: Patch dependencies
     working-directory: ./endcord-upstream
     run: |
       # Example: add missing dependency
       sed -i '/dependencies = \[/a \    "missing-package>=1.0.0",' pyproject.toml
   ```

4. **Switch to specific upstream version:**
   - Change the checkout step to use a specific tag:
   ```yaml
   - name: Checkout Upstream Endcord
     uses: actions/checkout@v4
     with:
       repository: sparklost/endcord
       path: endcord-upstream
       ref: '1.1.7'  # Use a stable version
   ```

### Issue 3: dpkg-deb Build Fails

**Symptoms:**
```
dpkg-deb: error: control directory has bad permissions
dpkg-deb: error: failed to make temporary file
```

**Causes:**
- Incorrect directory permissions
- Missing DEBIAN directory
- Empty control file

**Solutions:**

1. **Check file permissions in logs:**
   - Review the "Prepare Debian Package Structure" step
   - Ensure binary is executable

2. **Verify control file generation:**
   - Check "Generate Control and Desktop Files" step
   - Ensure no syntax errors in heredoc

3. **Inspect deb structure (add debug step):**
   ```yaml
   - name: Debug Debian Package
     working-directory: ./deb-build
     run: |
       tree endcord_${{ env.VERSION }}_amd64
       cat endcord_${{ env.VERSION }}_amd64/DEBIAN/control
   ```

### Issue 4: apt-ftparchive Fails

**Symptoms:**
```
E: Unable to read pool/main/endcord_*.deb
apt-ftparchive: error: No packages found
```

**Causes:**
- .deb file not copied to pool
- Incorrect path in apt-ftparchive command
- .deb file corrupted

**Solutions:**

1. **Verify .deb file exists:**
   Add debug step before apt-ftparchive:
   ```yaml
   - name: Debug Repository Structure
     run: |
       cd gh-pages-repo
       ls -lh pool/main/
       file pool/main/*.deb
   ```

2. **Check .deb file integrity:**
   ```yaml
   - name: Verify .deb File
     working-directory: ./deb-build
     run: |
       dpkg-deb --info endcord_${{ env.VERSION }}_amd64.deb
       dpkg-deb --contents endcord_${{ env.VERSION }}_amd64.deb
   ```

### Issue 5: Git Push to gh-pages Fails

**Symptoms:**
```
remote: Permission denied
error: failed to push some refs
```

**Causes:**
- Missing `contents: write` permission
- Branch protection rules
- Invalid GITHUB_TOKEN

**Solutions:**

1. **Verify workflow permissions:**
   Ensure this is in the workflow:
   ```yaml
   permissions:
     contents: write
   ```

2. **Check branch protection:**
   - Go to: Settings → Branches
   - Ensure gh-pages branch doesn't have strict protection
   - Allow force pushes if needed

3. **Use alternative push method:**
   ```yaml
   - name: Deploy to gh-pages
     uses: peaceiris/actions-gh-pages@v4
     with:
       github_token: ${{ secrets.GITHUB_TOKEN }}
       publish_dir: ./gh-pages-repo
       publish_branch: gh-pages
       force_orphan: true
   ```

### Issue 6: GitHub Pages Not Accessible

**Symptoms:**
- `https://username.github.io/endcord/` returns 404
- Repository files exist but page doesn't load

**Causes:**
- GitHub Pages not enabled
- Wrong branch or folder selected
- Build/deployment delay

**Solutions:**

1. **Enable GitHub Pages:**
   - Go to: Settings → Pages
   - Source: Deploy from a branch
   - Branch: gh-pages, folder: / (root)
   - Save

2. **Wait for deployment:**
   - GitHub Pages can take 5-10 minutes to deploy
   - Check: Settings → Pages for deployment status

3. **Verify branch content:**
   ```bash
   git fetch origin gh-pages
   git checkout gh-pages
   ls -la
   # Should see: pool/, dists/, public.key (after manual upload)
   ```

4. **Check repository visibility:**
   - Public repositories: Pages work immediately
   - Private repositories: Requires GitHub Pro/Team

### Issue 7: Users Can't Add Repository

**Symptoms:**
```
E: The repository '...' is not signed.
E: Failed to fetch...
```

**Causes:**
- Public key not uploaded to gh-pages
- Incorrect GPG key path in user instructions
- Repository URL incorrect

**Solutions:**

1. **Upload public key:**
   ```bash
   git checkout gh-pages
   cp /path/to/public.key .
   git add public.key
   git commit -m "Add public key"
   git push origin gh-pages
   ```

2. **Verify key is accessible:**
   ```bash
   curl https://username.github.io/endcord/public.key
   # Should return the GPG public key
   ```

3. **Test key import manually:**
   ```bash
   wget -qO - https://username.github.io/endcord/public.key | gpg --import
   gpg --list-keys
   ```

### Issue 8: Build Takes Too Long / Timeout

**Symptoms:**
```
Error: The operation was canceled.
Job exceeded the timeout limit
```

**Causes:**
- Nuitka compilation is slow on free runners (can take 30-60 minutes)
- Complex dependencies
- Insufficient resources
- Note: On free GitHub Actions runners, jobs can run for up to 6 hours (maximum allowed timeout). Our PyInstaller-based builds normally finish in 5–10 minutes, so this limit is not usually a concern for this workflow.

**Solutions:**

1. **Already using PyInstaller:**
   The workflow uses PyInstaller by default (5-10 min build time)

2. **Reduce build scope:**
   Use `--lite` flag if available:
   ```yaml
   uv run build.py --lite --onefile
   ```

3. **Upgrade to paid runners:**
   - Larger machines available in GitHub Team/Enterprise
   - Or use self-hosted runners

4. **Cache dependencies:**
   Add caching for uv:
   ```yaml
   - name: Install uv
     uses: astral-sh/setup-uv@v6
     with:
       enable-cache: true
   ```

## Debugging Workflow

### Add Debug Output

Insert debug steps in the workflow:

```yaml
- name: Debug Environment
  run: |
    echo "Working directory: $(pwd)"
    echo "Git status:"
    git status
    echo "Environment variables:"
    env | grep -E "(VERSION|GITHUB)" | sort
    echo "Directory structure:"
    find . -maxdepth 3 -type d
```

### Enable Step Debug Logging

1. Go to: Settings → Secrets and variables → Actions
2. Add repository secret: `ACTIONS_STEP_DEBUG` = `true`
3. Re-run the workflow
4. View extremely detailed logs

### Test Locally with Act

[Act](https://github.com/nektos/act) lets you test GitHub Actions locally:

```bash
# Install act
brew install act  # macOS
# or: curl https://raw.githubusercontent.com/nektos/act/master/install.sh | sudo bash

# Run the workflow locally
act workflow_dispatch -W .github/workflows/debian-repo.yml
```

**Note:** Local testing has limitations (secrets, runners, etc.)

## Monitoring and Alerting

### Email Notifications

GitHub automatically sends emails for workflow failures if you're watching the repository.

To enable:
1. Go to: Watch → Custom → Actions
2. Enable notifications for failed workflows

### Status Badge

Add a badge to README.md:

```markdown
[![Debian Repository](https://github.com/USERNAME/endcord/actions/workflows/debian-repo.yml/badge.svg)](https://github.com/USERNAME/endcord/actions/workflows/debian-repo.yml)
```

### Slack/Discord Integration

Use GitHub Actions marketplace actions:
- [action-slack](https://github.com/marketplace/actions/slack-notify)
- [discord-webhook-action](https://github.com/marketplace/actions/discord-webhook-action)

## Maintenance Commands

### Manually Trigger Workflow

```bash
# Using GitHub CLI (gh)
gh workflow run debian-repo.yml

# Or via web:
# Actions → Build and Publish Debian Repository → Run workflow
```

### Clean Old Packages

To remove old versions from the repository:

```bash
git checkout gh-pages
cd pool/main
# Keep only latest 5 versions
ls -t endcord_*.deb | tail -n +6 | xargs rm
# Regenerate repository metadata
cd ../../dists/stable/main/binary-amd64
apt-ftparchive packages ../../../../pool/main > Packages
cat Packages | gzip -9 > Packages.gz
cd ../../
apt-ftparchive release . > Release
# Re-sign
gpg --clearsign -o InRelease Release
gpg -abs -o Release.gpg Release
# Commit and push
git add .
git commit -m "Clean old packages"
git push origin gh-pages
```

### Force Rebuild All Metadata

If repository metadata is corrupted:

```bash
git checkout gh-pages
# Delete all metadata
rm -rf dists/
mkdir -p dists/stable/main/binary-amd64
cd dists/stable/main/binary-amd64
# Regenerate from scratch
apt-ftparchive packages ../../../../pool/main > Packages
gzip -9 -c Packages > Packages.gz
cd ../../
cat > Release <<-EOF
	Origin: Endcord-Unofficial
	Label: Endcord Community Repack
	Suite: stable
	Codename: stable
	Architectures: amd64
	Components: main
	Description: Unofficial community repack of Endcord - Not affiliated with the official Endcord team
	Date: $(date -R)
	EOF
apt-ftparchive release . >> Release
gpg --clearsign -o InRelease Release
gpg -abs -o Release.gpg Release
cd ../../..
git add .
git commit -m "Rebuild repository metadata"
git push origin gh-pages
```

## Getting Additional Help

1. **Check GitHub Actions documentation:**
   https://docs.github.com/actions

2. **Debian repository format:**
   https://wiki.debian.org/DebianRepository/Format

3. **Open an issue:**
   Include:
   - Link to failed workflow run
   - Relevant log excerpts
   - Steps to reproduce

4. **Community support:**
   - GitHub Discussions (if enabled)
   - Stack Overflow: [github-actions] tag

## Prevention Checklist

Before making changes to the workflow:

- [ ] Test syntax: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/debian-repo.yml'))"`
- [ ] Review diff carefully
- [ ] Test with `workflow_dispatch` before scheduling
- [ ] Have rollback plan (previous working version)
- [ ] Document changes in commit message
- [ ] Monitor first automated run

---

**Quick Diagnosis Commands:**

```bash
# Check if workflow file is valid
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/debian-repo.yml'))"

# List recent workflow runs
gh run list -w debian-repo.yml

# View logs of latest run
gh run view --log

# Check repository structure
curl -I https://username.github.io/endcord/pool/main/

# Verify GPG key is accessible
curl https://username.github.io/endcord/public.key

# Test package installation (in a Debian VM/container)
wget -qO - https://username.github.io/endcord/public.key | gpg --import
echo "deb https://username.github.io/endcord/ stable main" > /etc/apt/sources.list.d/endcord.list
apt update
apt install endcord
```

Remember: Most issues are configuration-related. Double-check secrets, permissions, and branch settings first!
