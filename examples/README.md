# Example suites

The published example is `demo/`: a two-scenario suite against [JSONPlaceholder](https://jsonplaceholder.typicode.com).

| File | Purpose |
|------|---------|
| `demo/suite.json` | Suite env: static `server`, derived `posts_url`, empty `user_id` |
| `demo/lookup_user.json` | Manual `user_id`, save response fields, reuse them in the next step |
| `demo/chain_posts.json` | List posts for that user, save a post id, fetch that post |

## What it demonstrates

- **Static environment values** — `server` is set in the suite file
- **Derived environment values** — `posts_url` is `{{ server }}/posts`
- **Manually provided values** — `user_id` is empty; fill it in **Missing environment values** (use `1`) before Run Tests
- **API chaining** — `save` copies JSON fields into `vars.*`
- **Passing vars between requests** — later steps use `{{ vars.looked_up_user_id }}`, `{{ vars.post_id }}`, and so on
- **Suite export** — `lookup_user` exports `user_name` / `user_email` for later members

## How to run

Open the web UI, select **demo**, enter `user_id` = `1`, then Run Tests. The published library is read-only; use **Copy to workspace** (or Save) to keep your own copy and private environment values.

Or from the CLI (after setting `user_id` in the suite env or via `--extra-env-file`):

```bash
printf '%s\n' '{"user_id":"1"}' > /tmp/demo-env.json
./bin/run.sh \
  --scenario-file ./examples/demo/suite.json \
  --scenario-extra-env /tmp/demo-env.json \
  --regression
```

API hosts must be an FQDN, not an IP address or localhost.

The `oauth2-server/` fixtures can stay on disk for local work; they are listed in `.gitignore` and are not part of the published repository.

## Token exchange

`token-exchange/` is the RunWay version of the token-exchange demo: dynamic client registration, a user sign-in, RFC 8693 token exchange, then refresh, introspection, and userinfo.

| File | Purpose |
|------|---------|
| `token-exchange/suite.json` | IdP environment: `server`, `api_key`, `redirect_uri` |
| `token-exchange/token_exchange.json` | The chained OAuth steps, including a `USER_AUTH` pause |

Fill the environment, then Run Tests. `server` is the issuer URL. `redirect_uri` is this RunWay app's origin plus `/scenario-auth/callback` (the field placeholder shows it). The run pauses at **user_authentication** and asks for a device code or an authorization code. The CLI stops on that step.
