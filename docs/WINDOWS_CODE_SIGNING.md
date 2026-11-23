# Windows code signing for Smara Desktop

Without a code-signing certificate, Windows shows "unknown publisher" warnings and SmartScreen may block the installer. This guide covers what you need to buy, how the build uses it, and how to test a clean install.

**Current status:** we are shipping unsigned for now, by decision. The build stays unsigned until `SMARA_SIGN_THUMBPRINT` is set, and nothing in this guide needs to change for that. Expect "unknown publisher" warnings on every install until a certificate is bought. For comparison, the Hermes setup on the same PC (`hermes-setup.exe`) is signed by Nous Research Inc., but the programs inside it, such as `hermes.exe`, are not.

## 1. What you need to buy (you do this)

Microsoft's own signing service (Azure Artifact Signing, formerly Trusted Signing) is not open to organisations registered in India, according to Microsoft's eligibility list. So use a certificate from a commercial certificate authority:

- **Type:** an OV (Organisation Validated) code-signing certificate for the company "Syntarus" (or the exact legal name). EV costs more and its SmartScreen advantage has changed; ask the vendor for the current position before paying for EV.
- **Vendors:** DigiCert, Sectigo, GlobalSign, or an Indian reseller of one of these. Reseller prices seen in research start at about ₹19,000 a year for OV, but check the current price and whether GST is included.
- **Key storage:** since June 2023, every publicly trusted code-signing certificate must keep its private key on a hardware token or in the CA's cloud signing service. Software-only .pfx files are no longer issued. Ask the vendor which option is included, and whether the token costs extra.
- **Documents:** the vendor will check the company's registration papers, a registered address, and a phone number. Start this early.

Once you have the certificate, install it on the build PC. For a token, install the vendor's driver and keep the token plugged in while you build. The token asks for its PIN when signing. Never paste the PIN into chat or into a script.

## 2. Build a signed installer

Find the certificate's thumbprint (run `certmgr.msc`, open the certificate, and copy the Thumbprint field, with spaces removed). Then, in PowerShell, from the repository root:

```powershell
$env:SMARA_SIGN_THUMBPRINT = '<40-character thumbprint>'
$env:SMARA_SIGN_TIMESTAMP_URL = 'http://timestamp.digicert.com'   # or the timestamp server your vendor gives you
.\scripts\build-smara-desktop.ps1
```

What the build does:
1. Signs the bundled executables (`smara-desktop.exe` and the `native\` tools) before packaging.
2. Passes the thumbprint to Tauri, which signs the app executable and the installer.
3. Checks that the installer's signature is valid and comes from your certificate.

If `SMARA_SIGN_THUMBPRINT` is not set, the build still runs, but it prints an UNSIGNED warning. Use unsigned builds only for local testing.

To check a finished installer by hand:

```powershell
.\scripts\sign-windows-files.ps1 -Files 'apps\desktop\src-tauri\target\release\bundle\nsis\Smara Desktop_0.1.24_x64-setup.exe' -VerifyOnly -Thumbprint $env:SMARA_SIGN_THUMBPRINT
```

The helper refuses files that are already signed, refuses a certificate it can't find, and refuses an untrusted chain. The helper itself was tested with a throwaway self-signed certificate. The real timestamp server has not been tested yet, because it needs the real certificate.

## 3. Test a clean install

You need at least one clean Windows 10 PC and one Windows 11 PC, or VMs, with no developer tools installed. Windows 10 Home and Windows 11 Home do not include Windows Sandbox or Hyper-V, so use real PCs, or VMs from another hypervisor.

For each machine:

1. Download the installer in Microsoft Edge, not by copying it over. This makes Windows apply the "downloaded from the internet" mark, and that is what SmartScreen checks.
2. Note whether SmartScreen appears. With a valid OV signature it should show the publisher name, not "unknown publisher".
3. Log in as a standard user (not an administrator). Copy the installer and run, as that user:

```powershell
.\scripts\test-desktop-install.ps1 -InstallerPath 'C:\path\to\Smara Desktop_0.1.24_x64-setup.exe'
```

The script installs silently, checks the signature of the installer and the installed program, checks the desktop shortcut, launches the app for 25 seconds, checks for crash events, and uninstalls. It writes a report and exits with 1 if any check fails.

4. Repeat on a PC where WebView2 is missing, if you have one. The installer then needs internet access to fetch it.
5. Run Microsoft Defender with the default settings on the installed program. A PyInstaller bundle can trigger false positives. Report any detection to Microsoft Security Intelligence, and note it in the test report.

## 4. What this does not cover yet

- **Signed auto-updates.** The app does not update itself. Shipping updates needs a separate signed update channel, which is a later task.
- **Unsigned bundled libraries.** The `_internal` folder contains `.dll` and `.pyd` files, which are not signed. Windows does not require them to be signed. If Defender flags them, sign them the same way with the same helper.
- **Certificate renewal.** OV certificates last one to three years. Renew it before the expiry date; the helper refuses to sign within seven days of expiry.

## Checklist before the first public download

- [ ] OV certificate issued, key on token or CA cloud, installed on the build PC
- [ ] Signed build from `build-smara-desktop.ps1`, with no UNSIGNED warning
- [ ] `test-desktop-install.ps1` reports ALL CHECKS PASSED on Windows 10 and Windows 11, as a standard user
- [ ] SmartScreen check done in Edge on both machines
- [ ] Defender scan of the installed program clean, or detections reported
