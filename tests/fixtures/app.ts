interface User {
  id: number;
  name: string;
  email: string;
}

function getUser(id: number): User {
  return { id, name: "Test", email: "test@example.com" };
}

export class UserService {
  private users: User[] = [];
  addUser(user: User): void {
    this.users.push(user);
  }
}
