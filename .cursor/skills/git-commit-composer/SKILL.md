---
name: git-commit-composer
description: >-
  Compõe commits semânticos e atômicos (Conventional Commits). Use quando o
  usuário pedir commit, revisar staged changes, ou agrupar alterações em
  histórico limpo.
---

# Git Commit Composer

Baseado na skill [Git Commit Composer](https://mcpmarket.com/tools/skills/git-commit-composer).

## Quando usar

- Usuário pede commit, push preparatório, ou revisão do que commitar
- Sessão com múltiplas mudanças que devem virar commits separados

## Fluxo

1. `git status` e `git diff` (staged + unstaged)
2. **Nunca** incluir: `.env`, credenciais, `venv/`, `venv311/`, `__pycache__/`, `*.log`
3. Agrupar hunks **atomicamente** (uma intenção por commit)
4. Escolher tipo Conventional Commit:

| Tipo | Uso |
|------|-----|
| `feat` | Nova funcionalidade |
| `fix` | Correção de bug |
| `refactor` | Mudança interna sem alterar comportamento |
| `test` | Só testes |
| `docs` | Só documentação (AGENTS.md, README) |
| `chore` | Manutenção, deps, config |

5. Subject: imperativo, ≤72 caracteres, sem ponto final
6. Corpo (opcional): explica **porquê**, não o quê (o diff mostra o quê)
7. **Sem** `Co-Authored-By` nem texto de IA no corpo

## Scopes sugeridos (este projeto)

`antispam`, `sync`, `db`, `ui`, `dox`, `tests`, `config`

Exemplos:
- `fix(antispam): excluir remetentes spam do bypass de conhecidos`
- `docs(dox): inicializar árvore AGENTS.md`

## Decisão de confiança

| Situação | Ação |
|----------|------|
| Uma feature coesa, arquivos relacionados | Um commit |
| DOX + código de feature na mesma sessão | Commits separados |
| Mudança mista (fix + feat) | Dividir em 2+ commits |
| Arquivos sensíveis no stage | Remover do stage e avisar |

## Execução neste servidor

O alias global de `git commit` falha com `--trailer`. Usar:

```bash
/usr/bin/git -c alias.commit= add <arquivos>
/usr/bin/git -c alias.commit= commit -m "$(cat <<'EOF'
tipo(escopo): descrição curta.

Corpo opcional.

EOF
)"
```

## Só commitar quando o usuário pedir

Não commitar automaticamente ao terminar tarefas — apenas quando houver pedido explícito.
