# GitHub 上传说明

目标账号：

```text
https://github.com/w1767178707
```

推荐仓库名：

```text
cyber-office-agent-os
```

## 方式一：GitHub 网页创建仓库后推送

先在 GitHub 网页创建一个空仓库：

```text
cyber-office-agent-os
```

不要勾选自动生成 README、LICENSE 或 .gitignore，因为项目里已经包含这些文件。

然后在项目根目录执行：

```bat
git init
git branch -M main
git add .
git commit -m "Initial commit: CyberOffice Agent OS"
git remote add origin https://github.com/w1767178707/cyber-office-agent-os.git
git push -u origin main
```

## 方式二：使用 GitHub CLI

先登录：

```bat
gh auth login
```

然后在项目根目录执行：

```bat
gh repo create w1767178707/cyber-office-agent-os --public --source=. --remote=origin --push
```

## Windows 一键脚本

项目提供：

```text
scripts/publish_github.bat
```

使用前请先确保 GitHub 上已经创建空仓库 `cyber-office-agent-os`，并且本机 Git 已经登录或配置了凭据。

执行：

```bat
scripts\publish_github.bat
```

## 上传前检查

```bat
git status
git log --oneline -5
```

上传后访问：

```text
https://github.com/w1767178707/cyber-office-agent-os
```
