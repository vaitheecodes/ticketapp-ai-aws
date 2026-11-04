import { useState } from "react";
import { Monitor, Moon, Sun } from "lucide-react";
import { Button } from "@/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/ui/dropdown-menu";
import { Tooltip } from "@/ui/tooltip";
import { getTheme, setTheme } from "@/utils/theme";

const OPTIONS = [
  ["system", "System", Monitor],
  ["light", "Light", Sun],
  ["dark", "Dark", Moon],
];

/* System / Light / Dark in one icon button: the slim top bar has no room
 * for a three-way switch, and System stays one click away. */
export default function ThemeMenu() {
  const [theme, setThemeState] = useState(getTheme);
  const [, , Icon] = OPTIONS.find(([value]) => value === theme);

  function choose(value) {
    setTheme(value);
    setThemeState(value);
  }

  return (
    <DropdownMenu>
      <Tooltip content="Theme">
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon" aria-label={`Theme: ${theme}`}>
            <Icon aria-hidden="true" />
          </Button>
        </DropdownMenuTrigger>
      </Tooltip>
      <DropdownMenuContent className="min-w-40">
        <DropdownMenuLabel>Theme</DropdownMenuLabel>
        <DropdownMenuRadioGroup value={theme} onValueChange={choose}>
          {OPTIONS.map(([value, label, ItemIcon]) => (
            <DropdownMenuRadioItem key={value} value={value}>
              <ItemIcon aria-hidden="true" />
              {label}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
