# Publish on GitHub

The repository homepage leads with the problem and a 12-second animation. GitHub Pages serves a clickable replay using synthetic scans, so visitors can inspect the workflow immediately. The full FastAPI application can be run locally from the same repository.

## 1. Commit the files on your Mac

Open **Terminal** in the project folder. For the original workspace, use the path below. If you extracted the ZIP, use the extracted project folder.

```sh
cd "/Users/mahdibayanloo/Documents/Codex/2026-11-23/new-chat"
git init -b main
git add .
git status --short
git commit -m "Add clinical document intake portfolio"
```

The staged files should include the source, README, synthetic fixtures, screenshots and workflows. The project ignores the local database, virtual environment, environment file, and dependency directories.

## 2. Publish the repository

In **GitHub Desktop**, choose **File → Add Local Repository** and select the project folder. Click **Publish repository**. Suggested name is `clinical-document-intake-rag`. For a public hiring portfolio, clear **Keep this code private** before publishing. [GitHub Desktop guide](https://docs.github.com/en/desktop/adding-and-cloning-repositories/adding-a-repository-from-your-local-computer-to-github-desktop).

Suggested description for the repository’s **About** panel

> An auditable lab-report review tool that turns noisy scans into structured results and links every cited answer back to the original image.

## 3. Turn on the no-install demo

1. Open the repository on GitHub and go to **Settings → Pages**.
2. Under **Build and deployment**, choose **GitHub Actions** as the source.
3. In **Actions**, select **Publish interactive demo** and choose **Run workflow** on `main`. When the deployment finishes, open **Settings → Pages** for the live URL. The workflow also runs on subsequent pushes to `main`. [GitHub Pages guide](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site).
4. Paste the live URL in the repository’s **About → Website** field and add a **Try the interactive demo** link alongside the links above the README walkthrough. Open the repository and the demo in a private browser window to confirm a visitor can access both.

The Pages build generates a self-contained HTML replay from an isolated intake run. The published page reads embedded snapshots, uses no API key, and has no upload control.
