# Quick Command Reference - Endcord Debian Repository

This is a quick reference for common commands. For detailed instructions, see [DEPLOYMENT_CHECKLIST.md](DEPLOYMENT_CHECKLIST.md).

---

## Initial Setup (One-Time)

### 1. Generate GPG Keys
```bash
./setup-gpg-keys.sh
```

### 2. Add GitHub Secrets (via Web UI)
```
Settings → Secrets → Actions → New repository secret

Secret 1: GPG_PRIVATE_KEY
Value: [content of gpg-exports/private.key]

Secret 2: GPG_PASSPHRASE  
Value: [your GPG key passphrase]
```

### 3. Enable GitHub Pages (via Web UI)
```
Settings → Pages
Source: Deploy from a branch
Branch: gh-pages
Folder: / (root)
```

### 4. Trigger Workflow (via Web UI)
```
Actions → Build and Publish Debian Repository → Run workflow
```

### 5. Upload Public Key
```bash
git fetch origin gh-pages
git checkout gh-pages
cp gpg-exports/public.key .
git add public.key
git commit -m "Add GPG public key"
git push origin gh-pages
git checkout copilot/create-github-pages-ppa
```

---

## User Installation Commands

### Add Repository Key
```bash
wget -qO - https://oichkatzelesfrettschen.github.io/endcord/public.key | \
  sudo gpg --dearmor -o /etc/apt/keyrings/endcord-archive-keyring.gpg
```

### Add Repository Source
```bash
echo "deb [signed-by=/etc/apt/keyrings/endcord-archive-keyring.gpg arch=amd64] https://oichkatzelesfrettschen.github.io/endcord/ stable main" | \
  sudo tee /etc/apt/sources.list.d/endcord.list
```

### Install Package
```bash
sudo apt update
sudo apt install endcord
```

### Update Package
```bash
sudo apt update
sudo apt upgrade
```

---

## Maintenance Commands

### Manual Workflow Trigger (GitHub CLI)
```bash
gh workflow run debian-repo.yml
```

### Check Workflow Status
```bash
gh run list --workflow=debian-repo.yml
```

### View Workflow Logs
```bash
gh run view --log
```

---

## Verification Commands

### Check Repository Structure
```bash
git checkout gh-pages
tree -L 3
# or
find . -type f | head -20
```

### Verify Repository URL
```bash
curl -I https://oichkatzelesfrettschen.github.io/endcord/
curl -I https://oichkatzelesfrettschen.github.io/endcord/public.key
```

### Test GPG Key Import
```bash
wget -qO - https://oichkatzelesfrettschen.github.io/endcord/public.key | gpg --import
gpg --list-keys
```

---

## Troubleshooting Commands

### View Workflow Logs
```bash
gh run list --workflow=debian-repo.yml --limit 5
gh run view [RUN_ID] --log
```

### Check GitHub Pages Status
```bash
curl -I https://oichkatzelesfrettschen.github.io/endcord/
```

### Verify GPG Keys
```bash
gpg --list-secret-keys --keyid-format LONG
gpg --list-keys
```

### Test Package Installation (in VM/container)
```bash
# Add repository (use commands from User Installation section above)
sudo apt update -o Debug::pkgAcquire::Worker=1
sudo apt install endcord --dry-run
```

---

## Repository Cleanup Commands

### Remove Old Packages (if needed)
```bash
git checkout gh-pages
cd pool/main
ls -lt endcord_*.deb | tail -n +6 | awk '{print $9}' | xargs rm -f
# Regenerate metadata
cd ../../dists/stable/main/binary-amd64
apt-ftparchive packages ../../../../pool/main > Packages
gzip -9 -c Packages > Packages.gz
cd ../../
apt-ftparchive release . > Release
gpg --clearsign -o InRelease Release
gpg -abs -o Release.gpg Release
git add .
git commit -m "Clean old packages"
git push origin gh-pages
```

---

## Local Testing Commands

### Build Locally (requires uv)
```bash
uv sync --group build
uv run build.py --onefile
```

### Test Debian Package Creation
```bash
# Create package structure
mkdir -p test-deb/endcord_1.0.0_amd64/DEBIAN
mkdir -p test-deb/endcord_1.0.0_amd64/usr/bin

# Copy binary
cp dist/endcord test-deb/endcord_1.0.0_amd64/usr/bin/

# Create control file
cat > test-deb/endcord_1.0.0_amd64/DEBIAN/control <<EOF
Package: endcord
Version: 1.0.0
Architecture: amd64
Maintainer: Test <test@example.com>
Description: Test package
EOF

# Build
dpkg-deb --build test-deb/endcord_1.0.0_amd64

# Inspect
dpkg-deb --info test-deb/endcord_1.0.0_amd64.deb
dpkg-deb --contents test-deb/endcord_1.0.0_amd64.deb
```

---

## URLs

- **Repository:** https://github.com/Oichkatzelesfrettschen/endcord
- **GitHub Pages:** https://oichkatzelesfrettschen.github.io/endcord/
- **Public Key:** https://oichkatzelesfrettschen.github.io/endcord/public.key
- **Workflow:** https://github.com/Oichkatzelesfrettschen/endcord/actions
- **Upstream:** https://github.com/sparklost/endcord

---

## Quick Links

- [DEPLOYMENT_CHECKLIST.md](DEPLOYMENT_CHECKLIST.md) - Complete deployment guide
- [DEBIAN_REPOSITORY.md](DEBIAN_REPOSITORY.md) - Full documentation
- [QUICKSTART.md](QUICKSTART.md) - Quick setup guide
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) - Problem solving
- [IMPLEMENTATION_SUMMARY.md](IMPLEMENTATION_SUMMARY.md) - Technical details

---

**Note:** Replace `oichkatzelesfrettschen` and `Oichkatzelesfrettschen` with your actual GitHub username if different.
