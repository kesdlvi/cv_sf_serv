"""
Relay server for network multiplayer.
Allows two players to connect through a central server, bypassing NAT/firewall issues.

Run this on a server with a public IP address:
    python relay_server.py --port 5555

Then both clients connect to this server instead of directly to each other.
"""
import socket
import threading
import pickle
import time
import argparse
import os
from typing import Optional, Dict

class RelayServer:
    """Relay server that forwards messages between two connected clients."""
    
    def __init__(self, host: str = "0.0.0.0", port: int = 5555):
        self.host = host
        self.port = port
        self.socket: Optional[socket.socket] = None
        
        # Connected clients: {player_id: (socket, address, thread)}
        self.clients: Dict[int, tuple] = {}
        self.clients_lock = threading.Lock()
        
        # Server state
        self.running = False
        
        # Logging control (to avoid spam when only one player connected)
        self._warned_missing_player = set()
        self._data_receive_count = {}
        
    def start(self):
        """Start the relay server."""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            self.socket.listen(2)  # Accept up to 2 connections
            self.running = True
            
            # Get and display the actual server IP addresses
            print(f"[Relay Server] Listening on {self.host}:{self.port}")
            print(f"[Relay Server] =========================================")
            print(f"[Relay Server] SERVER IP ADDRESSES:")
            
            # Get local IP address (preferred method - most reliable)
            server_ip = None
            try:
                # Connect to a dummy address to get local IP
                temp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                temp_sock.connect(("8.8.8.8", 80))
                local_ip = temp_sock.getsockname()[0]
                temp_sock.close()
                server_ip = local_ip
                print(f"[Relay Server]   Local IP: {local_ip}")
            except:
                print(f"[Relay Server]   Local IP: Could not determine")
            
            # Get hostname
            try:
                hostname = socket.gethostname()
                print(f"[Relay Server]   Hostname: {hostname}")
            except:
                pass
            
            # Get all network interfaces (fallback if local_ip method failed)
            if not server_ip:
                try:
                    hostname = socket.gethostname()
                    local_ips = socket.gethostbyname_ex(hostname)[2]
                    local_ips = [ip for ip in local_ips if not ip.startswith("127.")]
                    if local_ips:
                        server_ip = local_ips[0]  # Use first non-localhost IP
                        print(f"[Relay Server]   Network IPs: {', '.join(local_ips)}")
                except:
                    pass
            
            # Check for cloud platform environment variables (Railway, Render, etc.)
            railway_domain = os.environ.get('RAILWAY_PUBLIC_DOMAIN')
            render_domain = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
            heroku_domain = os.environ.get('DYNO')  # Heroku doesn't provide domain directly
            
            # Try to get public IP (for cross-network connections)
            public_ip = None
            try:
                import urllib.request
                public_ip = urllib.request.urlopen('https://api.ipify.org', timeout=3).read().decode('utf-8')
                print(f"[Relay Server]   Public IP: {public_ip} (use this if clients are on different network)")
            except:
                pass
            
            print(f"[Relay Server] =========================================")
            print(f"[Relay Server] Players should connect with:")
            
            # Priority: Cloud platform domain > Public IP > Local IP
            connection_host = None
            connection_port = self.port
            
            if railway_domain:
                connection_host = railway_domain
                print(f"[Relay Server]   RAILWAY DEPLOYMENT DETECTED")
                print(f"[Relay Server]   ⚠️  IMPORTANT: You need to add a TCP Proxy in Railway!")
                print(f"[Relay Server]   1. Go to Railway dashboard → Your Service → Settings")
                print(f"[Relay Server]   2. Add a TCP Proxy (under Networking section)")
                print(f"[Relay Server]   3. Railway will give you a TCP proxy hostname and port")
                print(f"[Relay Server]   4. Use the TCP proxy hostname/port for connections:")
                print(f"[Relay Server]      python main.py --relay --relay-ip <TCP_PROXY_HOSTNAME> --relay-port <TCP_PROXY_PORT>")
                print(f"[Relay Server]   5. Example: python main.py --relay --relay-ip tcp-proxy.production.up.railway.app --relay-port 12345")
                print(f"[Relay Server]   NOTE: Do NOT use the app domain ({railway_domain}) - use the TCP proxy!")
            elif render_domain:
                connection_host = render_domain
                print(f"[Relay Server]   RENDER DEPLOYMENT DETECTED")
                print(f"[Relay Server]   Use Render external hostname:")
                print(f"[Relay Server]   python main.py --relay --relay-ip {render_domain} --relay-port {connection_port}")
            elif public_ip and public_ip != server_ip:
                connection_host = public_ip
                print(f"[Relay Server]   For DIFFERENT network: python main.py --relay --relay-ip {public_ip} --relay-port {connection_port}")
            elif server_ip:
                connection_host = server_ip
                print(f"[Relay Server]   For SAME network: python main.py --relay --relay-ip {server_ip} --relay-port {connection_port}")
            
            if not connection_host:
                print(f"[Relay Server]   python main.py --relay --relay-ip <SERVER_IP> --relay-port {self.port}")
            
            print(f"[Relay Server] =========================================")
            print(f"[Relay Server] IMPORTANT: For Railway deployments:")
            print(f"[Relay Server]   - Add a TCP Proxy in Railway dashboard (Settings → Networking)")
            print(f"[Relay Server]   - Use the TCP proxy hostname/port, NOT the app domain")
            print(f"[Relay Server]   - Railway TCP proxy enables raw TCP socket connections")
            print(f"[Relay Server] =========================================")
            print(f"[Relay Server] NOTE: If ping fails but server is running, firewall may block ICMP.")
            print(f"[Relay Server] TCP connections (port {self.port}) may still work even if ping fails.")
            print(f"[Relay Server] =========================================")
            print(f"[Relay Server] Waiting for 2 players to connect...")
            
            # Accept connections
            while self.running:
                try:
                    conn, addr = self.socket.accept()
                    conn.settimeout(30.0)  # Longer timeout to keep connection alive
                    
                    with self.clients_lock:
                        # Find first available slot (1 or 2)
                        player_id = None
                        for slot in [1, 2]:
                            if slot not in self.clients:
                                player_id = slot
                                break
                        
                        if player_id is not None:
                            # Start handler thread for this client
                            thread = threading.Thread(
                                target=self._handle_client,
                                args=(conn, addr, player_id),
                                daemon=True
                            )
                            thread.start()
                            self.clients[player_id] = (conn, addr, thread)
                            print(f"[Relay Server] Player {player_id} connected from {addr}")
                            print(f"[Relay Server] Connected players: {list(self.clients.keys())}")
                        else:
                            # Both slots taken, reject connection
                            conn.close()
                            print(f"[Relay Server] Both player slots are taken, rejecting connection from {addr}")
                            
                except Exception as e:
                    if self.running:
                        print(f"[Relay Server] Error accepting connection: {e}")
                        import traceback
                        traceback.print_exc()
                        # Don't break - keep accepting connections
                        time.sleep(1)  # Wait a bit before retrying
                    else:
                        break  # Only break if server is stopping
                    
        except Exception as e:
            print(f"[Relay Server] Server error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.stop()
    
    def _handle_client(self, conn: socket.socket, addr: tuple, player_id: int):
        """Handle messages from a client."""
        buffer = b""
        expected_size = None
        
        try:
            print(f"[Relay Server] Player {player_id} handler started, waiting for data...")
            # Set a longer timeout to keep connection alive
            conn.settimeout(30.0)
            while self.running:
                try:
                    data = conn.recv(4096)
                except socket.timeout:
                    # Timeout is normal - connection is still alive, just no data yet
                    # Check if connection is still valid
                    try:
                        # Try to peek at the socket to see if it's still connected
                        conn.settimeout(0.1)
                        test_data = conn.recv(1, socket.MSG_PEEK)
                        conn.settimeout(30.0)
                        if not test_data:
                            print(f"[Relay Server] Player {player_id} connection closed (peek returned empty)")
                            break
                    except:
                        print(f"[Relay Server] Player {player_id} connection error during timeout check")
                        break
                    continue  # Normal timeout, keep looping
                except Exception as e:
                    print(f"[Relay Server] Error receiving from player {player_id}: {e}")
                    import traceback
                    traceback.print_exc()
                    break
                
                if not data:
                    # Empty data means client closed connection
                    print(f"[Relay Server] Player {player_id} disconnected (connection closed by client)")
                    break
                
                # Only print first few data receives to avoid spam
                if not hasattr(self, '_data_receive_count'):
                    self._data_receive_count = {}
                if player_id not in self._data_receive_count:
                    self._data_receive_count[player_id] = 0
                if self._data_receive_count[player_id] < 5:
                    print(f"[Relay Server] Player {player_id} sent {len(data)} bytes")
                    self._data_receive_count[player_id] += 1
                
                buffer += data
                
                # Parse messages (format: <size><pickled_data>)
                while len(buffer) >= 4:
                    if expected_size is None:
                        try:
                            expected_size = int.from_bytes(buffer[:4], byteorder='big')
                            if expected_size <= 0 or expected_size > 10 * 1024 * 1024:  # Sanity check: max 10MB
                                print(f"[Relay Server] Invalid message size from player {player_id}: {expected_size}")
                                buffer = buffer[4:]  # Skip this message
                                expected_size = None
                                continue
                            buffer = buffer[4:]
                        except Exception as e:
                            print(f"[Relay Server] Error reading message size from player {player_id}: {e}")
                            buffer = b""  # Clear buffer on error
                            expected_size = None
                            break
                    
                    if len(buffer) >= expected_size:
                        try:
                            message = pickle.loads(buffer[:expected_size])
                            buffer = buffer[expected_size:]
                            expected_size = None
                            
                            # Forward message to the other player
                            self._forward_message(player_id, message)
                            
                        except Exception as e:
                            print(f"[Relay Server] Error parsing message from player {player_id}: {e}")
                            import traceback
                            traceback.print_exc()
                            # Skip this message and continue
                            if expected_size and expected_size <= len(buffer):
                                buffer = buffer[expected_size:]
                            else:
                                buffer = b""
                            expected_size = None
                    else:
                        break  # Need more data
                        
        except Exception as e:
            print(f"[Relay Server] Handler error for player {player_id}: {e}")
        finally:
            # Clean up client
            with self.clients_lock:
                if player_id in self.clients:
                    del self.clients[player_id]
                try:
                    conn.close()
                except:
                    pass
            print(f"[Relay Server] Player {player_id} handler closed")
    
    def _forward_message(self, from_player: int, message: dict):
        """Forward a message from one player to the other."""
        # Determine target player (1 -> 2, 2 -> 1)
        target_player = 2 if from_player == 1 else 1
        
        with self.clients_lock:
            if target_player in self.clients:
                target_socket, _, _ = self.clients[target_player]
                try:
                    # Serialize and send message
                    data = pickle.dumps(message)
                    size = len(data).to_bytes(4, byteorder='big')
                    target_socket.sendall(size + data)
                    # Only print first few forwards to avoid spam
                    if message.get("type") == "input" and message.get("frame", 0) < 5:
                        print(f"[Relay Server] Forwarded message from Player {from_player} to Player {target_player}")
                except Exception as e:
                    print(f"[Relay Server] Error forwarding to player {target_player}: {e}")
            else:
                # Target player not connected yet, drop message (this is normal)
                # Only print once to avoid spam when waiting for second player
                if target_player not in self._warned_missing_player:
                    print(f"[Relay Server] Player {target_player} not connected yet, waiting... (messages will be dropped until connected)")
                    self._warned_missing_player.add(target_player)
                pass
    
    def stop(self):
        """Stop the relay server."""
        self.running = False
        
        with self.clients_lock:
            for player_id, (conn, _, _) in list(self.clients.items()):
                try:
                    conn.close()
                except:
                    pass
            self.clients.clear()
        
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
        
        print("[Relay Server] Server stopped")


def main():
    import os
    parser = argparse.ArgumentParser(description="Relay server for network multiplayer")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)")
    # Check for PORT environment variable (used by Railway, Render, Heroku, etc.)
    default_port = int(os.environ.get('PORT', 5555))
    parser.add_argument("--port", type=int, default=default_port, help=f"Port to listen on (default: {default_port}, or $PORT env var)")
    
    args = parser.parse_args()
    
    server = RelayServer(host=args.host, port=args.port)
    
    try:
        server.start()
    except KeyboardInterrupt:
        print("\n[Relay Server] Shutting down...")
        server.stop()


if __name__ == "__main__":
    main()


