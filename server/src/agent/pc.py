"""Tools that reach the owner's own PC through the paired helper (the same channel Smara Desktop uses)."""
from src.bridge.device import run_pc_tool


async def pc_files(app, ctx, args: dict) -> dict:
    """list / read / write in the Smara Shared folder on the owner's PC. Writes only get here after the owner's tap."""
    return await run_pc_tool(app.state.pool, ctx.tenant_id, ctx.id, "files",
                             {k: args.get(k) for k in ("action", "path", "text", "overwrite")})
