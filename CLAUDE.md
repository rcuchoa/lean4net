## PM2 Services

| Port | Name | Type |
|------|------|------|
| 5000 | lean4net-webapp-5000 | Flask (backend/webapp/server.py) |
| 8000 | lean4net-api-8000 | FastAPI (backend/lean-api, uvicorn) |

**Terminal Commands:**
```bash
pm2 start ecosystem.config.cjs   # First time
pm2 start all                    # After first time
pm2 stop all / pm2 restart all
pm2 start lean4net-webapp-5000 / pm2 stop lean4net-webapp-5000
pm2 start lean4net-api-8000 / pm2 stop lean4net-api-8000
pm2 logs / pm2 status / pm2 monit
pm2 save                         # Save process list
pm2 resurrect                    # Restore saved list
```
