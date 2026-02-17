
import asyncio
from app.services.plex_service import plex_service
import httpx

async def main():
    print("Fetching libraries...")
    try:
        # We need a token. We can use the admin token from settings
        token = plex_service.admin_token
        
        async with httpx.AsyncClient() as client:
            url = f"{plex_service.server_url}/library/sections"
            resp = await client.get(url, headers=plex_service._headers(token))
            
            if resp.status_code == 200:
                data = resp.json()
                libraries = data.get("MediaContainer", {}).get("Directory", [])
                
                print(f"{'ID':<5} | {'Type':<10} | {'Title'}")
                print("-" * 40)
                for lib in libraries:
                    print(f"{lib['key']:<5} | {lib['type']:<10} | {lib['title']}")
            else:
                print(f"Failed to get libraries: {resp.status_code}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
