import { FolderOpen, LogOut, MessageSquarePlus } from "lucide-react";
import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import { useMe } from "../../api/me";
import { useAuth } from "../../auth/AuthContext";
import { Avatar } from "../../components/Avatar";
import { Button } from "../../components/Button";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { LogoMark } from "../../components/LogoMark";
import { ThemeToggle } from "../../components/ThemeToggle";
import { brand } from "../../config/brand";
import { useChat } from "../chat/ChatContext";
import { AccessCard } from "./AccessCard";
import { DocumentList } from "./DocumentList";

export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const me = useMe();
  const { signOut } = useAuth();
  const chat = useChat();
  const navigate = useNavigate();
  const location = useLocation();
  const [confirmNew, setConfirmNew] = useState(false);
  if (!me.data) return null;
  const user = me.data;

  const newChat = () => {
    if (chat.messages.length > 2) setConfirmNew(true);
    else {
      chat.clear();
      if (location.pathname !== "/dashboard") navigate("/dashboard");
      onNavigate?.();
    }
  };

  return (
    <nav
      aria-label="Sidebar"
      className="flex h-full w-full flex-col gap-4 bg-spruce-deep px-4 py-5 text-on-spruce-deep"
    >
      <Link to="/dashboard" className="flex items-center gap-2" onClick={onNavigate}>
        <LogoMark size={28} />
        <span className="font-display text-panel font-bold">{brand.name}</span>
      </Link>
      <div className="flex items-center gap-3" data-testid="user-card">
        <Avatar userId={user.user_id} name={user.display_name} />
        <span className="truncate font-medium">{user.display_name}</span>
      </div>
      <AccessCard me={user} />
      <DocumentList onOpen={onNavigate} />
      <div className="flex flex-col gap-1 border-t border-white/10 pt-3">
        <Button variant="sidebar" className="justify-start" onClick={newChat}>
          <MessageSquarePlus size={16} aria-hidden="true" />
          New chat
        </Button>
        {/* Cosmetic only: the backend enforces the admin role on every admin endpoint. */}
        {user.roles.includes("admin") && (
          <Link
            to="/admin/files"
            onClick={onNavigate}
            className="inline-flex items-center gap-2 rounded-xl px-4 py-2 text-ui font-medium text-on-spruce-deep hover:bg-white/10"
          >
            <FolderOpen size={16} aria-hidden="true" />
            Manage files
          </Link>
        )}
        <div className="flex items-center justify-between gap-2">
          <ThemeToggle className="text-on-spruce-deep hover:bg-white/10" />
          <Button variant="sidebar" onClick={signOut}>
            <LogOut size={16} aria-hidden="true" />
            Sign out
          </Button>
        </div>
      </div>
      <ConfirmDialog
        open={confirmNew}
        onOpenChange={setConfirmNew}
        title="Start a new chat?"
        description="This clears the current conversation."
        confirmLabel="New chat"
        onConfirm={() => {
          chat.clear();
          if (location.pathname !== "/dashboard") navigate("/dashboard");
          onNavigate?.();
        }}
      />
    </nav>
  );
}
