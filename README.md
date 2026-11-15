# appdata

各个 App 的公开静态数据（法规数据、科普文章等）。

这个仓库是**公开**的：App 下载数据时不带登录信息。请不要在这里放任何密钥、用户数据或不能公开的内容。

## 目录约定

每个 App 一个目录，各自有构建脚本、测试和发布流程：

| 目录 | App | 发布标签 | 固定的说明文件地址 |
|---|---|---|---|
| `eatclear/` | 吃得明白（EatClear） | `eatclear-v<版本>` | `releases/download/eatclear-latest/manifest.json` |

新增 App 时：

1. 新建 `<app>/` 目录
2. 新建 `.github/workflows/<app>-data.yml`，只监听 `<app>/**` 和 `<app>-v*` 标签
3. 发布时把 `manifest.json` 覆盖到固定的 `<app>-latest` Release

**不要使用 GitHub 的 "Latest release"**：一个仓库只有一个，多个 App 会互相覆盖。每个 App 用自己的 `<app>-latest`。

## 关于在中国大陆下载

GitHub 在中国大陆的访问不稳定。各个 App 都应该：

- 在 App 里内置一份数据，保证没有网络时也能用
- 下载失败时安静地继续使用当前数据

`manifest.json` 里的 `urls` 可以写多个下载地址。以后有了国内的存储或 CDN，在构建时多加一个 `--url-prefix` 即可，不需要更新 App。
