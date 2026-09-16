module.exports = {
  apps: [
    {
      name: 'lean4net-webapp-5000',
      cwd: __dirname,
      script: 'backend/webapp/server.py',
      interpreter: 'python3',
      env: {
        PORT: '5000',
        LEAN4NET_ADMIN_USER: process.env.LEAN4NET_ADMIN_USER,
        LEAN4NET_ADMIN_PASSWORD_HASH: process.env.LEAN4NET_ADMIN_PASSWORD_HASH,
        LEAN4NET_SECRET_KEY: process.env.LEAN4NET_SECRET_KEY,
      },
    },
    {
      name: 'lean4net-api-8000',
      cwd: __dirname + '/backend/lean-api',
      script: '/opt/anaconda3/bin/uvicorn',
      interpreter: 'none',
      args: 'app.main:app --host 127.0.0.1 --port 8000',
    },
  ],
}
