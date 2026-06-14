import React, { useEffect, useRef, useState } from "react";

/**
 * 原地编辑输入框。挂载即自动聚焦并全选。
 * - 回车 / 失焦 → onCommit(当前值)
 * - Esc → onCancel()
 * 提交与取消的「是否真正改名」判断交给调用方处理。
 */
export default function InlineEdit({ initial = "", onCommit, onCancel, className = "" }) {
  const [value, setValue] = useState(initial);
  const ref = useRef(null);

  useEffect(() => {
    ref.current?.focus();
    ref.current?.select();
  }, []);

  return (
    <input
      ref={ref}
      className={"inline-edit " + className}
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") onCommit(value);
        else if (e.key === "Escape") onCancel();
      }}
      onBlur={() => onCommit(value)}
    />
  );
}
