import os

from databricks.sdk import WorkspaceClient
from posit import connect
from shiny import reactive, render
from shiny.express import input, session, ui


DATABRICKS_HOST_FROM_ENV = os.environ.get("DATABRICKS_HOST", "")


ui.page_opts(title="Azure OAuth Diagnostic", fillable=False)


@reactive.calc
def session_token():
    return session.http_conn.headers.get("Posit-Connect-User-Session-Token")


@reactive.calc
def connect_client():
    return connect.Client()


@reactive.calc
def current_content():
    return connect_client().content.get()


@reactive.calc
def associations():
    try:
        return list(current_content().oauth.associations.find())
    except Exception as e:
        return e


with ui.card():
    ui.card_header("Status")

    @render.ui
    def status_display():
        token = session_token()
        items = [ui.tags.li(f"Session token present: {bool(token)}")]
        if token:
            items.append(ui.tags.li(f"Token prefix: {token[:20]}..."))
        try:
            c = current_content()
            items.append(ui.tags.li(f"Content GUID: {c.get('guid')}"))
            items.append(ui.tags.li(f"Content title: {c.get('title')}"))
        except Exception as e:
            items.append(ui.tags.li(f"Content lookup error: {type(e).__name__}: {e}"))
        if DATABRICKS_HOST_FROM_ENV:
            items.append(ui.tags.li(f"DATABRICKS_HOST (env): {DATABRICKS_HOST_FROM_ENV}"))
        else:
            items.append(ui.tags.li(
                "DATABRICKS_HOST env var not set — Azure integrations don't auto-inject "
                "a workspace host, so enter it manually below."
            ))
        return ui.tags.ul(*items)


with ui.card():
    ui.card_header("Associated integrations on this content")

    @render.ui
    def integrations_display():
        assocs = associations()
        if isinstance(assocs, Exception):
            return ui.div(
                ui.tags.strong(f"Error ({type(assocs).__name__})"),
                ui.tags.pre(str(assocs)),
                class_="alert alert-danger",
            )
        if not assocs:
            return ui.div(
                "No integrations associated with this content.",
                class_="alert alert-warning",
            )
        rows = []
        for a in assocs:
            guid = a.get("oauth_integration_guid")
            name = a.get("oauth_integration_name")
            type_ = a.get("oauth_integration_type")
            rows.append(ui.tags.li(f"{name} — type={type_} — guid={guid}"))
        return ui.tags.ul(*rows)


with ui.card():
    ui.card_header("Step 1: Exchange session token for Azure AD credentials")
    ui.markdown(
        "Calls `connect.Client().oauth.get_credentials(token)` to retrieve the "
        "Azure AD OAuth access token from Posit Connect. This does **not** touch "
        "Azure Databricks yet — it just proves the credential exchange works."
    )
    ui.input_action_button("get_creds", "Get credentials", class_="btn-primary")

    @render.ui
    def creds_result():
        if input.get_creds() == 0:
            return ui.div("Not yet called.", class_="text-muted")
        token = session_token()
        if not token:
            return ui.div("No session token available.", class_="alert alert-warning")
        try:
            creds = connect_client().oauth.get_credentials(token)
            access_token = creds.get("access_token", "")
            return ui.div(
                ui.tags.strong("SUCCESS"),
                ui.tags.pre(f"keys: {list(creds.keys())}"),
                ui.tags.pre(f"token_type: {creds.get('token_type')}"),
                ui.tags.pre(f"access_token (first 40 chars): {str(access_token)[:40]}..."),
                ui.tags.pre(f"access_token length: {len(str(access_token))}"),
                class_="alert alert-success",
            )
        except Exception as e:
            return ui.div(
                ui.tags.strong(f"ERROR ({type(e).__name__})"),
                ui.tags.pre(str(e)),
                class_="alert alert-danger",
            )


with ui.card():
    ui.card_header("Step 2: Verify Azure Databricks auth without a warehouse")
    ui.markdown(
        "Uses the Azure AD access token from Step 1 to call **`WorkspaceClient.current_user.me()`** "
        "against an Azure Databricks workspace. This control-plane SCIM call does not "
        "require a running SQL warehouse or compute cluster — it only proves that the "
        "AAD token is accepted by Azure Databricks and identifies the user.\n\n"
        "Enter your Azure Databricks workspace host below "
        "(e.g., `https://adb-xxxxxxxxxxxxxxx.x.azuredatabricks.net`). "
        "Pre-populated from `DATABRICKS_HOST` if set."
    )
    ui.input_text(
        "workspace_host",
        "Azure Databricks workspace host",
        value=DATABRICKS_HOST_FROM_ENV,
        placeholder="https://adb-xxxxxxxxxxxxxxx.x.azuredatabricks.net",
        width="100%",
    )
    ui.input_action_button("verify_auth", "Verify Azure Databricks auth", class_="btn-primary")

    @render.ui
    def verify_result():
        if input.verify_auth() == 0:
            return ui.div("Not yet called.", class_="text-muted")
        token = session_token()
        if not token:
            return ui.div("No session token available.", class_="alert alert-warning")
        host = input.workspace_host().strip()
        if not host:
            return ui.div("Enter a workspace host.", class_="alert alert-warning")
        try:
            creds = connect_client().oauth.get_credentials(token)
            access_token = creds.get("access_token")
            if not access_token:
                return ui.div(
                    ui.tags.strong("ERROR"),
                    ui.tags.pre("No access_token in credentials response."),
                    ui.tags.pre(f"keys: {list(creds.keys())}"),
                    class_="alert alert-danger",
                )
        except Exception as e:
            return ui.div(
                ui.tags.strong(f"Credential exchange failed ({type(e).__name__})"),
                ui.tags.pre(str(e)),
                class_="alert alert-danger",
            )
        try:
            w = WorkspaceClient(host=host, token=access_token)
            me = w.current_user.me()
            emails = [e.value for e in (me.emails or []) if e.value]
            return ui.div(
                ui.tags.strong("SUCCESS — Azure Databricks accepted the AAD token"),
                ui.tags.pre(f"user_name: {me.user_name}"),
                ui.tags.pre(f"display_name: {me.display_name}"),
                ui.tags.pre(f"id: {me.id}"),
                ui.tags.pre(f"emails: {emails}"),
                ui.tags.pre(f"active: {me.active}"),
                class_="alert alert-success",
            )
        except Exception as e:
            return ui.div(
                ui.tags.strong(f"Azure Databricks auth failed ({type(e).__name__})"),
                ui.tags.pre(f"host: {host}"),
                ui.tags.pre(str(e)),
                class_="alert alert-danger",
            )
